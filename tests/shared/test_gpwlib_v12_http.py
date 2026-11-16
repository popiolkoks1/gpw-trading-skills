"""gpwlib 1.2 - http: cache keys, encodings, rate limit (fake clock), cache/offline, retries."""

import datetime as dt
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared"))

from gpwlib import __version__, http  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 6))
        self.t += s


class FakeResp:
    def __init__(self, body: bytes, status=200, ctype="text/html; charset=utf-8"):
        self._body, self.status = body, status
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(monkeypatch):
    """Replace the network, the limiter clock and back-off sleeps."""
    clock = FakeClock()
    calls = []
    script = []  # items: bytes | Exception | (bytes, ctype)

    def fake_urlopen(req, timeout=30):
        calls.append(req)
        item = script.pop(0) if script else b"ok"
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple):
            return FakeResp(item[0], ctype=item[1])
        return FakeResp(item)

    monkeypatch.setattr(http, "_urlopen", fake_urlopen)
    monkeypatch.setattr(http, "LIMITER", http.RateLimiter(1.0, clock=clock, sleep=clock.sleep))
    backoffs = []
    monkeypatch.setattr(http, "_sleep", lambda s: backoffs.append(s))
    return {"calls": calls, "script": script, "clock": clock, "backoffs": backoffs}


# --- keys / params ---------------------------------------------------------------

def test_cache_key_ignores_param_order_and_url_query_split():
    a = http.cache_key("GET", "https://x.pl/a?b=2&a=1")
    b = http.cache_key("get", "https://X.pl/a", {"a": 1, "b": 2})
    c = http.cache_key("GET", "https://x.pl/a", [("b", "2"), ("a", "1")])
    assert a == b == c
    assert http.cache_key("POST", "https://x.pl/a", {"a": 1, "b": 2}) != a
    f1 = http.cache_key("POST", "https://x.pl/a", form={"t[]": ["RB", "Q"], "date": "1"})
    f2 = http.cache_key("POST", "https://x.pl/a", form=[("date", "1"), ("t[]", "Q"), ("t[]", "RB")])
    f3 = http.cache_key("POST", "https://x.pl/a", form={"t[]": ["RB"], "date": "1"})
    assert f1 == f2 != f3


def test_build_url_repeats_list_values():
    u = http.build_url("https://x.pl/p", {"a": ["1", "2"], "b": None})
    assert u == "https://x.pl/p?a=1&a=2&b="
    assert http.build_url("https://x.pl/p?z=1", {"a": 1}) == "https://x.pl/p?z=1&a=1"


# --- encodings -------------------------------------------------------------------

def test_decode_utf8_and_declared_charsets():
    assert http.decode("Zażółć".encode("utf-8")) == ("Zażółć", "utf-8")
    raw = "Łódź".encode("iso-8859-2")
    assert http.decode(raw, "text/html; charset=ISO-8859-2") == ("Łódź", "iso-8859-2")
    meta = b'<html><head><meta charset="windows-1250"></head>' + "Śląsk".encode("cp1250")
    text, enc = http.decode(meta)
    assert enc == "cp1250" and "Śląsk" in text


def test_decode_guesses_cp1250_vs_iso_8859_2():
    # "Ś" / "ś" / "ź" live in 0x80-0x9F in cp1250 (control chars in ISO-8859-2)
    t, enc = http.decode("Świętokrzyska źródło".encode("cp1250"))
    assert enc == "cp1250" and t == "Świętokrzyska źródło"
    t, enc = http.decode("Łódź Kraków".encode("iso-8859-2"))
    assert enc == "iso-8859-2" and t == "Łódź Kraków"


def test_decode_bad_declared_charset_falls_back():
    text, enc = http.decode("zł".encode("utf-8"), "text/html; charset=bogus-xyz")
    assert text == "zł" and enc == "utf-8"


# --- rate limit with a fake clock ---------------------------------------------------

def test_rate_limiter_per_host_with_fake_clock():
    c = FakeClock()
    lim = http.RateLimiter(1.0, clock=c, sleep=c.sleep)
    assert lim.wait("www.gpw.pl") == 0.0          # first call: no wait
    assert lim.wait("www.gpw.pl") == pytest.approx(1.0)
    c.t += 0.4
    assert lim.wait("www.gpw.pl") == pytest.approx(0.6)
    assert lim.wait("filings.xbrl.org") == 0.0    # other host is independent
    c.t += 5
    assert lim.wait("WWW.GPW.PL") == 0.0          # host names are case-insensitive
    assert c.slept == [1.0, 0.6]


def test_rate_limiter_floor_is_one_second():
    c = FakeClock()
    lim = http.RateLimiter(0.1, clock=c, sleep=c.sleep)
    assert lim.min_interval == 1.0
    lim.wait("h")
    assert lim.wait("h") == pytest.approx(1.0)
    lim2 = http.RateLimiter(2.5, clock=c, sleep=c.sleep)
    lim2.wait("h")
    assert lim2.wait("h") == pytest.approx(2.5)


def test_rate_limiter_with_frozen_clock_still_spaces_slots():
    lim = http.RateLimiter(1.0, clock=lambda: 50.0, sleep=lambda s: None)
    lim.wait("h")
    assert lim.wait("h") == pytest.approx(1.0)
    assert lim.wait("h") == pytest.approx(2.0)   # next slot is after the previous planned one


def test_set_min_interval(monkeypatch):
    monkeypatch.setattr(http, "LIMITER", http.RateLimiter(1.0))
    assert http.set_min_interval(0.2) == 1.0
    assert http.set_min_interval(3) == 3.0


def test_requests_are_rate_limited(net, tmp_path):
    http.get("https://www.gpw.pl/a", cache_dir=tmp_path, use_cache=False)
    http.get("https://www.gpw.pl/b", cache_dir=tmp_path, use_cache=False)
    http.get("https://filings.xbrl.org/c", cache_dir=tmp_path, use_cache=False)
    assert net["clock"].slept == [1.0]


# --- cache / offline -------------------------------------------------------------------

def test_get_caches_and_identifies_itself(net, tmp_path):
    net["script"].append("Treść".encode("utf-8"))
    r1 = http.get("https://www.gpw.pl/x", {"q": 1}, cache_dir=tmp_path)
    r2 = http.get("https://www.gpw.pl/x", {"q": 1}, cache_dir=tmp_path)
    assert r1.text == r2.text == "Treść"
    assert not r1.from_cache and r2.from_cache
    assert len(net["calls"]) == 1
    ua = net["calls"][0].get_header("User-agent")
    assert ua.startswith("gpw-trading-skills/{}".format(__version__)) and "Mozilla" not in ua
    files = list((tmp_path / "http" / "www.gpw.pl").glob("*.json"))
    assert len(files) == 1


def test_ttl_zero_refetches_but_writes(net, tmp_path):
    net["script"] += [b"one", b"two"]
    http.get("https://h.pl/x", cache_dir=tmp_path, ttl_seconds=0)
    r = http.get("https://h.pl/x", cache_dir=tmp_path, ttl_seconds=0)
    assert r.text == "two" and len(net["calls"]) == 2
    assert http.get("https://h.pl/x", cache_dir=tmp_path, offline=True).text == "two"


def test_expired_ttl_refetches(net, tmp_path, monkeypatch):
    net["script"] += [b"old", b"new"]
    http.get("https://h.pl/x", cache_dir=tmp_path, ttl_seconds=60)
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=120)
    monkeypatch.setattr(http, "_now_utc", lambda: later)
    assert http.get("https://h.pl/x", cache_dir=tmp_path, ttl_seconds=60).text == "new"
    # ttl None never expires
    net["script"].append(b"never")
    assert http.get("https://h.pl/x", cache_dir=tmp_path, ttl_seconds=None).text == "new"


def test_valid_after_invalidates_older_copies(net, tmp_path):
    net["script"] += [b"partial day", b"full day"]
    http.post("https://h.pl/list", {"date": "12-11-2026"}, cache_dir=tmp_path, ttl_seconds=None)
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    r = http.post("https://h.pl/list", {"date": "12-11-2026"}, cache_dir=tmp_path, ttl_seconds=None,
                  valid_after=future)
    assert r.text == "full day" and not r.from_cache


def test_offline_hit_and_miss(net, tmp_path):
    with pytest.raises(http.OfflineMiss):
        http.get("https://h.pl/none", cache_dir=tmp_path, offline=True)
    assert net["calls"] == []
    net["script"].append(b"cached")
    http.get("https://h.pl/one", cache_dir=tmp_path)
    r = http.get("https://h.pl/one", cache_dir=tmp_path, offline=True, ttl_seconds=0)
    assert r.text == "cached" and r.from_cache
    assert issubclass(http.OfflineMiss, http.HTTPFetchError)


def test_use_cache_false_bypasses(net, tmp_path):
    net["script"] += [b"a", b"b"]
    http.get("https://h.pl/x", cache_dir=tmp_path, use_cache=False)
    assert http.get("https://h.pl/x", cache_dir=tmp_path, use_cache=False).text == "b"
    assert not (tmp_path / "http").exists()


def test_corrupt_cache_entry_is_refetched(net, tmp_path):
    net["script"] += [b"first", b"second"]
    http.get("https://h.pl/x", cache_dir=tmp_path)
    f = next((tmp_path / "http" / "h.pl").glob("*.json"))
    f.write_text("{not json", encoding="utf-8")
    assert http.get("https://h.pl/x", cache_dir=tmp_path).text == "second"


def test_post_encodes_repeated_keys(net, tmp_path):
    http.post("https://www.gpw.pl/ajaxindex.php", [("typeRaports[]", "RB"), ("typeRaports[]", "Q"), ("date", "12-11-2026")],
              cache_dir=tmp_path)
    req = net["calls"][0]
    assert req.get_method() == "POST"
    assert req.data == b"typeRaports%5B%5D=RB&typeRaports%5B%5D=Q&date=12-11-2026"
    assert "x-www-form-urlencoded" in req.get_header("Content-type")


def test_response_json_and_charset_from_header(net, tmp_path):
    net["script"].append(('{"a": "Łódź"}'.encode("iso-8859-2"), "application/json; charset=iso-8859-2"))
    r = http.get("https://h.pl/j", cache_dir=tmp_path)
    assert r.json() == {"a": "Łódź"} and r.encoding == "iso-8859-2"


# --- retries / errors -------------------------------------------------------------------

def _http_error(code):
    return urllib.error.HTTPError("https://h.pl/x", code, "err", {}, None)


def test_retry_on_5xx_then_success(net, tmp_path):
    net["script"] += [_http_error(503), _http_error(502), b"fine"]
    r = http.get("https://h.pl/x", cache_dir=tmp_path, retries=3)
    assert r.text == "fine"
    assert net["backoffs"] == [1.5, 3.0]


def test_4xx_fails_immediately_with_status(net, tmp_path):
    net["script"] += [_http_error(403), b"never"]
    with pytest.raises(http.HTTPFetchError) as ei:
        http.get("https://h.pl/x", cache_dir=tmp_path)
    assert ei.value.status == 403 and len(net["calls"]) == 1


def test_timeouts_exhaust_retries(net, tmp_path):
    net["script"] += [urllib.error.URLError("timed out"), TimeoutError("t"), ConnectionResetError("r")]
    with pytest.raises(http.HTTPFetchError) as ei:
        http.get("https://h.pl/x", cache_dir=tmp_path, retries=3)
    assert "3 attempt" in str(ei.value) and len(net["calls"]) == 3
    assert not isinstance(ei.value, http.OfflineMiss)
