"""Polite HTTP client with an on-disk cache, shared by every gpwlib data source.

Rules (see docs/DATA_SOURCES.md, "Politeness and terms"):

* identifying User-Agent ``gpw-trading-skills/<version>`` - never a spoofed
  browser string, and no attempt to get around a site's bot protection (the
  GPW WAF refuses scripted downloads of attachment PDFs; we accept that);
* at most one request per host every ``MIN_INTERVAL_S`` seconds (default
  1.0 s, override with ``GPW_SKILLS_MIN_INTERVAL`` or :func:`set_min_interval`;
  values below 1.0 s are raised to 1.0 s);
* retries with exponential back-off on HTTP 5xx / 429, timeouts and connection
  errors; other 4xx fail at once (:class:`HTTPFetchError` with ``status``);
* on-disk cache keyed by method + URL + sorted query/form parameters.

Cache semantics (``get`` / ``post``)
------------------------------------
``ttl_seconds``  float seconds a cached copy stays fresh; ``None`` = never
                 expires (immutable data, e.g. a past day's report list);
                 ``0`` = always refetch (but still write the cache so a later
                 ``offline=True`` run can use it).
``valid_after``  optional aware/naive-UTC ``datetime``: a cached copy retrieved
                 before it is treated as expired (e.g. a day's ESPI list fetched
                 while that day was still running).
``offline``      never touch the network: return the cached copy whatever its
                 age, raise :class:`OfflineMiss` when there is none.
``use_cache``    ``False`` bypasses the cache completely (no read, no write).

Binary files (``get_bytes``)
----------------------------
:func:`get_bytes` has the same rate limiter, User-Agent, retries and cache
semantics but never decodes: the cache keeps the exact bytes in
``<sha256>.bin`` next to a small ``<sha256>.bin.json`` metadata file, so PDF,
XLSX and ZIP downloads are not corrupted. ``Response.content`` holds the
bytes. Its cache entries are separate from the text cache of :func:`get`.

The cache lives under ``<cache_dir>/http/<host>/<sha256>.json`` where
``cache_dir`` defaults to ``gpw_archive.default_cache_dir()``
(``~/.cache/gpw-trading-skills`` or ``$GPW_SKILLS_CACHE``). It stays on the
user's machine and must not be redistributed.

Everything that touches the network goes through module attributes
(``_urlopen``, ``_sleep``, ``LIMITER``) so tests can replace them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import __version__

USER_AGENT = "gpw-trading-skills/{} (educational market-analysis skills)".format(__version__)
MIN_INTERVAL_FLOOR_S = 1.0
DEFAULT_TTL_S = 3600.0
CACHE_SCHEMA = 1
RETRY_STATUSES = (429, 500, 502, 503, 504)
BACKOFF_BASE_S = 1.5

Params = Union[None, Dict[str, object], Sequence[Tuple[str, object]]]


class HTTPFetchError(RuntimeError):
    """The resource could not be fetched (network error, HTTP error status)."""

    def __init__(self, message: str, url: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.url = url
        self.status = status


class OfflineMiss(HTTPFetchError):
    """``offline=True`` and the resource is not in the cache."""


class Response:
    """Result of :func:`get` / :func:`post` / :func:`get_bytes`.

    ``text`` is the decoded body, ``content`` the raw bytes. A text response
    read from the cache keeps only the decoded text, so its ``content`` is the
    text re-encoded with ``encoding``; use :func:`get_bytes` for binary files
    (PDF, XLSX, ZIP), whose cache keeps the exact bytes. For a binary response
    ``text`` is decoded lazily (best effort) on first access.
    """

    __slots__ = ("url", "status", "_text", "encoding", "retrieved_at", "from_cache", "headers", "_content")

    def __init__(self, url: str, status: int, text: Optional[str], encoding: str, retrieved_at: str,
                 from_cache: bool, headers: Optional[Dict[str, str]] = None, content: Optional[bytes] = None):
        self.url = url
        self.status = status
        self._text = text
        self.encoding = encoding
        self.retrieved_at = retrieved_at
        self.from_cache = from_cache
        self.headers = headers or {}
        self._content = content

    @property
    def text(self) -> str:
        if self._text is None:
            raw = self._content or b""
            self._text, enc = decode(raw, self.headers.get("content-type"))
            if not self.encoding:
                self.encoding = enc
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value

    @property
    def content(self) -> bytes:
        if self._content is None:
            try:
                self._content = (self._text or "").encode(self.encoding or "utf-8")
            except (LookupError, UnicodeEncodeError):
                self._content = (self._text or "").encode("utf-8")
        return self._content

    def json(self):
        return json.loads(self.text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        size = len(self._content) if self._content is not None else len(self._text or "")
        return "Response({} {} {} {}{})".format(self.status, self.url, size,
                                               "bytes" if self._content is not None else "chars",
                                               ", cached" if self.from_cache else "")


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

class RateLimiter:
    """Minimum interval between two requests to the same host.

    ``clock`` and ``sleep`` are injectable so the logic is testable with a fake
    clock.
    """

    def __init__(self, min_interval: float = MIN_INTERVAL_FLOOR_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = max(MIN_INTERVAL_FLOOR_S, float(min_interval))
        self.clock = clock
        self.sleep = sleep
        self._last: Dict[str, float] = {}

    def wait(self, host: str) -> float:
        """Block until ``host`` may be called again; return the seconds slept."""
        host = (host or "").lower()
        now = self.clock()
        last = self._last.get(host)
        slept = 0.0
        if last is not None:
            gap = self.min_interval - (now - last)
            if gap > 0:
                self.sleep(gap)
                slept = gap
                now = self.clock()
                # a fake/coarse clock may not advance - never record a time
                # earlier than the planned slot
                now = max(now, last + self.min_interval)
        self._last[host] = now
        return slept


def _env_interval() -> float:
    try:
        return float(os.environ.get("GPW_SKILLS_MIN_INTERVAL", MIN_INTERVAL_FLOOR_S))
    except ValueError:
        return MIN_INTERVAL_FLOOR_S


LIMITER = RateLimiter(_env_interval())
_urlopen = urllib.request.urlopen
_sleep = time.sleep


def set_min_interval(seconds: float) -> float:
    """Change the per-host interval (never below 1.0 s). Returns the value used."""
    LIMITER.min_interval = max(MIN_INTERVAL_FLOOR_S, float(seconds))
    return LIMITER.min_interval


# --------------------------------------------------------------------------
# Helpers: parameters, cache keys, encodings
# --------------------------------------------------------------------------

def _pairs(params: Params) -> List[Tuple[str, str]]:
    """Normalise a dict (values may be lists) or a sequence of pairs."""
    if not params:
        return []
    items: Iterable = params.items() if isinstance(params, dict) else params
    out: List[Tuple[str, str]] = []
    for key, val in items:
        if isinstance(val, (list, tuple)):
            out.extend((str(key), "" if v is None else str(v)) for v in val)
        else:
            out.append((str(key), "" if val is None else str(val)))
    return out


def build_url(url: str, params: Params = None) -> str:
    pairs = _pairs(params)
    if not pairs:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(pairs)


def cache_key(method: str, url: str, params: Params = None, form: Params = None) -> str:
    """sha256 of method + URL (its own query sorted too) + sorted params/form."""
    split = urllib.parse.urlsplit(url)
    base = urllib.parse.urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path, "", ""))
    q = sorted(urllib.parse.parse_qsl(split.query, keep_blank_values=True) + _pairs(params))
    f = sorted(_pairs(form))
    blob = json.dumps([method.upper(), base, q, f], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_\-]+)""", re.I)
_ALIASES = {"latin2": "iso-8859-2", "iso8859-2": "iso-8859-2", "iso-8859-2": "iso-8859-2",
            "windows-1250": "cp1250", "cp1250": "cp1250", "utf8": "utf-8", "utf-8": "utf-8"}


def _norm_enc(name: str) -> str:
    name = (name or "").strip().lower()
    return _ALIASES.get(name, name)


def decode(raw: bytes, content_type: Optional[str] = None) -> Tuple[str, str]:
    """Decode bytes to text, returning ``(text, encoding)``.

    Order: charset from the Content-Type header, then ``<meta charset>`` in the
    first 4 KB, then strict UTF-8, then a Polish legacy guess: bytes 0x80-0x9F
    are control characters in ISO-8859-2 but letters (Ś, ś, Ź, ź, ...) in
    cp1250, so their presence means cp1250; otherwise ISO-8859-2. A declared
    charset that fails to decode falls through to the next step.
    """
    candidates: List[str] = []
    if content_type:
        m = re.search(r"charset\s*=\s*\"?([A-Za-z0-9_\-]+)", content_type, re.I)
        if m:
            candidates.append(_norm_enc(m.group(1)))
    m = _META_CHARSET.search(raw[:4096])
    if m:
        candidates.append(_norm_enc(m.group(1).decode("ascii", "ignore")))
    candidates.append("utf-8")
    for enc in candidates:
        try:
            return raw.decode(enc), enc
        except (LookupError, UnicodeDecodeError):
            continue
    if any(0x80 <= b <= 0x9F for b in raw):
        return raw.decode("cp1250", errors="replace"), "cp1250"
    return raw.decode("iso-8859-2", errors="replace"), "iso-8859-2"


def default_cache_dir() -> Path:
    from .gpw_archive import default_cache_dir as _d  # local import: avoid a cycle
    return _d()


def _cache_file(cache_dir: Path, url: str, key: str) -> Path:
    host = urllib.parse.urlsplit(url).netloc.lower() or "local"
    host = re.sub(r"[^a-z0-9.\-]", "_", host)
    return Path(cache_dir) / "http" / host / "{}.json".format(key)


def _parse_ts(text: str) -> Optional[dt.datetime]:
    try:
        ts = dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    return ts


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read_cache(path: Path) -> Optional[Dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("schema") != CACHE_SCHEMA or "text" not in payload:
        return None
    return payload


def _is_fresh(payload: Dict, ttl_seconds: Optional[float], valid_after: Optional[dt.datetime]) -> bool:
    ts = _parse_ts(payload.get("retrieved_at", ""))
    if ts is None:
        return False
    if valid_after is not None:
        va = valid_after if valid_after.tzinfo else valid_after.replace(tzinfo=dt.timezone.utc)
        if ts < va:
            return False
    if ttl_seconds is None:
        return True
    return (_now_utc() - ts).total_seconds() < ttl_seconds


def _write_cache(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

def _fetch(method: str, url: str, body: Optional[bytes], headers: Dict[str, str],
           timeout: float, retries: int) -> Tuple[int, bytes, Dict[str, str]]:
    host = urllib.parse.urlsplit(url).netloc
    last_err: Optional[BaseException] = None
    last_status: Optional[int] = None
    for attempt in range(max(1, retries)):
        if attempt:
            _sleep(BACKOFF_BASE_S * (2 ** (attempt - 1)))
        LIMITER.wait(host)
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with _urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", None) or resp.getcode() or 200
                hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
            return int(status), raw, hdrs
        except urllib.error.HTTPError as exc:
            last_err, last_status = exc, exc.code
            if exc.code not in RETRY_STATUSES:
                raise HTTPFetchError("HTTP {} for {}".format(exc.code, url), url, exc.code)
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_err, last_status = exc, None
    raise HTTPFetchError("{} unreachable after {} attempt(s): {}".format(url, max(1, retries), last_err),
                         url, last_status)


def request(method: str, url: str, params: Params = None, form: Params = None, *,
            cache_dir: Optional[Path] = None, ttl_seconds: Optional[float] = DEFAULT_TTL_S,
            valid_after: Optional[dt.datetime] = None, offline: bool = False,
            use_cache: bool = True, timeout: float = 30, retries: int = 3,
            headers: Optional[Dict[str, str]] = None) -> Response:
    method = method.upper()
    full_url = build_url(url, params)
    key = cache_key(method, url, params, form)
    path = _cache_file(Path(cache_dir) if cache_dir else default_cache_dir(), url, key)
    cached = _read_cache(path) if use_cache else None
    if offline:
        if cached is None:
            raise OfflineMiss("Not in cache (offline): {} {}".format(method, full_url), full_url)
        return Response(cached["url"], cached.get("status", 200), cached["text"], cached.get("encoding", "utf-8"),
                        cached["retrieved_at"], True, cached.get("headers"))
    if cached is not None and _is_fresh(cached, ttl_seconds, valid_after):
        return Response(cached["url"], cached.get("status", 200), cached["text"], cached.get("encoding", "utf-8"),
                        cached["retrieved_at"], True, cached.get("headers"))
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.8"}
    hdrs.update(headers or {})
    body = None
    if method == "POST":
        body = urllib.parse.urlencode(_pairs(form)).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    status, raw, rhdrs = _fetch(method, full_url, body, hdrs, timeout, retries)
    text, enc = decode(raw, rhdrs.get("content-type"))
    retrieved = _now_utc().isoformat(timespec="seconds")
    keep = {k: v for k, v in rhdrs.items() if k in ("content-type", "last-modified", "etag")}
    if use_cache:
        _write_cache(path, {"schema": CACHE_SCHEMA, "method": method, "url": full_url,
                            "form": _pairs(form), "status": status, "encoding": enc,
                            "retrieved_at": retrieved, "headers": keep, "text": text})
    return Response(full_url, status, text, enc, retrieved, False, keep, content=raw)


def get(url: str, params: Params = None, *, cache_dir: Optional[Path] = None,
        ttl_seconds: Optional[float] = DEFAULT_TTL_S, valid_after: Optional[dt.datetime] = None,
        offline: bool = False, use_cache: bool = True, timeout: float = 30, retries: int = 3,
        headers: Optional[Dict[str, str]] = None) -> Response:
    """Polite cached GET. See the module docstring for the cache semantics."""
    return request("GET", url, params, None, cache_dir=cache_dir, ttl_seconds=ttl_seconds,
                   valid_after=valid_after, offline=offline, use_cache=use_cache,
                   timeout=timeout, retries=retries, headers=headers)


def post(url: str, form: Params = None, *, params: Params = None, cache_dir: Optional[Path] = None,
         ttl_seconds: Optional[float] = DEFAULT_TTL_S, valid_after: Optional[dt.datetime] = None,
         offline: bool = False, use_cache: bool = True, timeout: float = 30, retries: int = 3,
         headers: Optional[Dict[str, str]] = None) -> Response:
    """Polite cached form POST (``application/x-www-form-urlencoded``).

    ``form`` may be a dict (list values repeat the key, e.g.
    ``{"typeRaports[]": ["RB", "Q"]}``) or a sequence of ``(key, value)`` pairs.
    """
    return request("POST", url, params, form, cache_dir=cache_dir, ttl_seconds=ttl_seconds,
                   valid_after=valid_after, offline=offline, use_cache=use_cache,
                   timeout=timeout, retries=retries, headers=headers)


# --------------------------------------------------------------------------
# Binary downloads
# --------------------------------------------------------------------------

def _bytes_cache_files(cache_dir: Path, url: str, key: str) -> Tuple[Path, Path]:
    base = _cache_file(cache_dir, url, key)
    return base.with_suffix(".bin"), base.with_suffix(".bin.json")


def _read_bytes_cache(data_path: Path, meta_path: Path) -> Optional[Tuple[Dict, bytes]]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = data_path.read_bytes()
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("schema") != CACHE_SCHEMA:
        return None
    if meta.get("size") is not None and meta["size"] != len(raw):
        return None  # truncated write
    return meta, raw


def _bytes_response(meta: Dict, raw: bytes, from_cache: bool) -> Response:
    return Response(meta["url"], meta.get("status", 200), None, "", meta["retrieved_at"], from_cache,
                    meta.get("headers"), content=raw)


def get_bytes(url: str, params: Params = None, *, cache_dir: Optional[Path] = None,
              ttl_seconds: Optional[float] = DEFAULT_TTL_S, valid_after: Optional[dt.datetime] = None,
              offline: bool = False, use_cache: bool = True, timeout: float = 60, retries: int = 3,
              headers: Optional[Dict[str, str]] = None) -> Response:
    """Polite cached GET of a binary file; ``Response.content`` is the raw bytes.

    Same limiter, User-Agent, retries and cache arguments as :func:`get`
    (``ttl_seconds=None`` for immutable files, ``offline=True`` raises
    :class:`OfflineMiss` when not cached). Nothing is decoded or rewritten.
    """
    full_url = build_url(url, params)
    key = cache_key("GET", url, params, None)
    data_path, meta_path = _bytes_cache_files(Path(cache_dir) if cache_dir else default_cache_dir(), url, key)
    cached = _read_bytes_cache(data_path, meta_path) if use_cache else None
    if offline:
        if cached is None:
            raise OfflineMiss("Not in cache (offline): GET {}".format(full_url), full_url)
        return _bytes_response(cached[0], cached[1], True)
    if cached is not None and _is_fresh(cached[0], ttl_seconds, valid_after):
        return _bytes_response(cached[0], cached[1], True)
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "pl,en;q=0.8"}
    hdrs.update(headers or {})
    status, raw, rhdrs = _fetch("GET", full_url, None, hdrs, timeout, retries)
    retrieved = _now_utc().isoformat(timespec="seconds")
    keep = {k: v for k, v in rhdrs.items() if k in ("content-type", "content-length", "last-modified", "etag")}
    meta = {"schema": CACHE_SCHEMA, "method": "GET", "url": full_url, "status": status,
            "retrieved_at": retrieved, "headers": keep, "size": len(raw)}
    if use_cache:
        data_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = data_path.with_suffix(".bin.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, data_path)
        _write_cache(meta_path, meta)
    return _bytes_response(meta, raw, False)


def drop_cached(url: str, params: Params = None, *, cache_dir: Optional[Path] = None,
                method: str = "GET", form: Params = None) -> int:
    """Remove the cached copies (text and binary) of one request, e.g. after
    the caller found the body unusable (an HTML error page instead of a PDF).
    Returns the number of files removed."""
    key = cache_key(method, url, params, form)
    root = Path(cache_dir) if cache_dir else default_cache_dir()
    paths = [_cache_file(root, url, key), *_bytes_cache_files(root, url, key)]
    n = 0
    for p in paths:
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
