"""Minimal stdlib PDF text extractor (enough for OFE/PPK portfolio tables).

Scope
-----
Fund reports ("Roczna struktura aktywów") are machine-generated PDFs with
FlateDecode content streams, TrueType fonts (literal strings, WinAnsi) and
Type0/Identity-H fonts whose glyph codes need the font's ``/ToUnicode`` CMap.
This module:

1. indexes every object, including objects packed inside ``/ObjStm`` streams;
2. walks the page tree in order and reads each page's content stream(s) and
   font resources;
3. interprets the text operators (``Tf Td TD Tm T* Tj TJ ' "``) to get text
   runs with their (x, y) position;
4. groups runs into visual lines (same y within a tolerance) ordered by x.

It is deliberately small: no images, no encryption (encrypted PDFs raise
:class:`PDFTextError`), no LZW/ASCII85 filters (streams with unsupported
filters are skipped), no vertical writing. Scanned PDFs have no text and give
an empty result - the caller must tell the user to supply CSV/XLSX instead.

The input is untrusted data: everything is parsed with regular expressions
and ``zlib``; nothing is executed, and sizes are bounded.
"""

from __future__ import annotations

import re
import zlib
from typing import Dict, List, Optional, Tuple

MAX_PDF_BYTES = 60 * 1024 * 1024
MAX_STREAM_BYTES = 50 * 1024 * 1024
LINE_Y_TOLERANCE = 2.0  # points; table rows in fund reports are ~6-10 pt apart


class PDFTextError(ValueError):
    """The file is not a PDF we can read."""


_OBJ_RE = re.compile(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b")
_REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R\b")


def _decompress(data: bytes) -> Optional[bytes]:
    try:
        d = zlib.decompressobj()
        out = d.decompress(data, MAX_STREAM_BYTES)
        return out
    except zlib.error:
        # some producers append garbage / CRLF; try trimming the tail
        for cut in (1, 2):
            try:
                return zlib.decompress(data[:-cut])
            except zlib.error:
                continue
    return None


class PDFDoc:
    def __init__(self, data: bytes):
        if len(data) > MAX_PDF_BYTES:
            raise PDFTextError("PDF too large ({} bytes)".format(len(data)))
        if not data.lstrip()[:5] == b"%PDF-":
            raise PDFTextError("not a PDF file (missing %PDF- header)")
        self.data = data
        self.objs: Dict[int, bytes] = {}       # object number -> dictionary/body bytes (no stream)
        self.streams: Dict[int, bytes] = {}    # object number -> raw (still encoded) stream bytes
        self._index()
        if re.search(rb"/Encrypt\s+\d+\s+\d+\s+R", data):
            raise PDFTextError("encrypted PDF - export the table to CSV/XLSX instead")

    # ---------------- object index ----------------
    def _index(self) -> None:
        data = self.data
        positions = [(m.start(), m.end(), int(m.group(1))) for m in _OBJ_RE.finditer(data)]
        for i, (start, body_start, num) in enumerate(positions):
            end = data.find(b"endobj", body_start)
            if end < 0:
                end = positions[i + 1][0] if i + 1 < len(positions) else len(data)
            body = data[body_start:end]
            sm = re.search(rb"stream\r?\n", body)
            if sm and b"/Length" in body[:sm.start()] or (sm and body[:sm.start()].strip().startswith(b"<<")):
                dict_part = body[:sm.start()]
                raw = body[sm.end():]
                es = raw.rfind(b"endstream")
                if es >= 0:
                    raw = raw[:es]
                lm = re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", dict_part)
                if lm and int(lm.group(1)) <= len(raw):
                    raw = raw[:int(lm.group(1))]
                self.objs[num] = dict_part  # later duplicates (incremental updates) win
                self.streams[num] = raw
            else:
                self.objs[num] = body
                self.streams.pop(num, None)
        # objects packed in object streams
        for num in list(self.streams):
            d = self.objs.get(num, b"")
            if b"/ObjStm" not in d:
                continue
            content = self.stream(num)
            if content is None:
                continue
            nm = re.search(rb"/N\s+(\d+)", d)
            fm = re.search(rb"/First\s+(\d+)", d)
            if not nm or not fm:
                continue
            n, first = int(nm.group(1)), int(fm.group(1))
            header = content[:first].split()
            pairs = [(int(header[k]), int(header[k + 1])) for k in range(0, min(len(header), 2 * n) - 1, 2)]
            for k, (onum, off) in enumerate(pairs):
                start = first + off
                end = first + pairs[k + 1][1] if k + 1 < len(pairs) else len(content)
                self.objs.setdefault(onum, content[start:end])

    def stream(self, num: int) -> Optional[bytes]:
        raw = self.streams.get(num)
        if raw is None:
            return None
        d = self.objs.get(num, b"")
        if b"/FlateDecode" in d or b"/Fl " in d or b"/Fl/" in d:
            return _decompress(raw)
        if b"/Filter" in d:
            return None  # unsupported filter
        return raw

    def resolve(self, value: bytes) -> bytes:
        m = re.fullmatch(rb"\s*(\d+)\s+(\d+)\s+R\s*", value)
        if m:
            return self.objs.get(int(m.group(1)), b"")
        return value

    # ---------------- dictionaries ----------------
    @staticmethod
    def dict_value(d: bytes, key: bytes) -> Optional[bytes]:
        """Value of ``/key`` in a dictionary (ref, name, number, array or nested dict)."""
        m = re.search(rb"/" + key + rb"(?![A-Za-z0-9])\s*", d)
        if not m:
            return None
        i = m.end()
        if d[i:i + 2] == b"<<":
            depth, j = 0, i
            while j < len(d):
                if d[j:j + 2] == b"<<":
                    depth += 1
                    j += 2
                    continue
                if d[j:j + 2] == b">>":
                    depth -= 1
                    j += 2
                    if depth == 0:
                        return d[i:j]
                    continue
                j += 1
            return d[i:]
        if d[i:i + 1] == b"[":
            j = d.find(b"]", i)
            return d[i:j + 1] if j >= 0 else d[i:]
        rm = re.match(rb"(\d+\s+\d+\s+R)", d[i:])
        if rm:
            return rm.group(1)
        vm = re.match(rb"(/?[^\s/<>\[\]()]+)", d[i:])
        return vm.group(1) if vm else None

    def pages(self) -> List[int]:
        root = None
        for num, d in self.objs.items():
            if re.search(rb"/Type\s*/Catalog", d):
                root = d
                break
        out: List[int] = []
        if root is not None:
            pref = self.dict_value(root, b"Pages")
            if pref:
                m = _REF_RE.search(pref)
                if m:
                    self._walk(int(m.group(1)), out, set())
        if not out:  # fallback: every /Type/Page in file order
            out = [n for n, d in sorted(self.objs.items()) if re.search(rb"/Type\s*/Page(?![s])", d)]
        return out

    def _walk(self, num: int, out: List[int], seen: set) -> None:
        if num in seen or len(seen) > 100000:
            return
        seen.add(num)
        d = self.objs.get(num, b"")
        if re.search(rb"/Type\s*/Pages", d):
            kids = self.dict_value(d, b"Kids") or b""
            for m in _REF_RE.finditer(kids):
                self._walk(int(m.group(1)), out, seen)
        elif re.search(rb"/Type\s*/Page", d):
            out.append(num)

    def page_resources(self, num: int) -> bytes:
        d = self.objs.get(num, b"")
        res = self.dict_value(d, b"Resources")
        seen = set()
        while res is None:  # inherited from parent
            parent = self.dict_value(d, b"Parent")
            m = _REF_RE.search(parent or b"")
            if not m or int(m.group(1)) in seen:
                return b""
            seen.add(int(m.group(1)))
            d = self.objs.get(int(m.group(1)), b"")
            res = self.dict_value(d, b"Resources")
        return self.resolve(res)

    def page_contents(self, num: int) -> bytes:
        d = self.objs.get(num, b"")
        c = self.dict_value(d, b"Contents")
        if not c:
            return b""
        if c.startswith(b"["):
            refs = [int(m.group(1)) for m in _REF_RE.finditer(c)]
        else:
            m = _REF_RE.search(c)
            refs = [int(m.group(1))] if m else []
            # a Contents ref may point to an array object
            if refs and refs[0] not in self.streams:
                arr = self.objs.get(refs[0], b"")
                refs = [int(x.group(1)) for x in _REF_RE.finditer(arr)]
        parts = [self.stream(r) or b"" for r in refs]
        return b"\n".join(parts)

    def fonts(self, resources: bytes) -> Dict[str, "Font"]:
        fdict = self.resolve(self.dict_value(resources, b"Font") or b"")
        out: Dict[str, Font] = {}
        for m in re.finditer(rb"/([^\s/<>\[\]()]+)\s*(\d+)\s+(\d+)\s+R", fdict):
            name = m.group(1).decode("latin-1")
            fobj = self.objs.get(int(m.group(2)), b"")
            out[name] = Font(self, fobj)
        return out


class Font:
    def __init__(self, doc: PDFDoc, fobj: bytes):
        self.two_byte = b"/Identity-H" in fobj or b"/Type0" in fobj
        self.cmap: Dict[int, str] = {}
        tu = doc.dict_value(fobj, b"ToUnicode")
        if tu:
            m = _REF_RE.search(tu)
            if m:
                data = doc.stream(int(m.group(1)))
                if data:
                    self.cmap = parse_cmap(data)

    def decode(self, raw: bytes) -> str:
        if self.two_byte:
            codes = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
        else:
            codes = list(raw)
        out = []
        for c in codes:
            if c in self.cmap:
                out.append(self.cmap[c])
            elif not self.two_byte:
                out.append(bytes([c]).decode("cp1252", errors="replace"))
            else:
                out.append("�")
        return "".join(out)


def _hex_to_text(h: bytes) -> str:
    h = re.sub(rb"\s", b"", h)
    if len(h) % 2:
        h += b"0"
    b = bytes.fromhex(h.decode("ascii"))
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def parse_cmap(data: bytes) -> Dict[int, str]:
    """``bfchar`` / ``bfrange`` entries of a ToUnicode CMap -> {code: text}."""
    out: Dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = _hex_to_text(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for m in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", block):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            if hi - lo > 65535:
                continue
            dst = m.group(3)
            if dst.startswith(b"["):
                items = re.findall(rb"<([0-9A-Fa-f]*)>", dst)
                for k, it in enumerate(items):
                    if lo + k <= hi:
                        out[lo + k] = _hex_to_text(it)
            else:
                base = re.sub(rb"\s", b"", dst[1:-1])
                bval = int(base, 16)
                width = len(base)
                for k in range(hi - lo + 1):
                    out[lo + k] = _hex_to_text(("{:0" + str(width) + "X}").format(bval + k).encode())
    return out


# ---------------- content stream interpretation ----------------

_TOKEN = re.compile(
    rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)"   # literal string (one nesting level)
    rb"|<[0-9A-Fa-f\s]*>"                            # hex string
    rb"|\[|\]"
    rb"|/[^\s/<>\[\]()]*"
    rb"|[+-]?(?:\d+\.?\d*|\.\d+)"
    rb"|[A-Za-z'\"*]+[0-9]?\*?"
    rb"|<<|>>", re.S)

_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _literal(tok: bytes) -> bytes:
    s = tok[1:-1]
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b"\\" and i + 1 < len(s):
            n = s[i + 1:i + 2]
            if n in _ESC:
                out += _ESC[n]
                i += 2
                continue
            om = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
            if om:
                out.append(int(om.group(0), 8) & 0xFF)
                i += 1 + len(om.group(0))
                continue
            if n in (b"\r", b"\n"):
                i += 2
                continue
            i += 1
            continue
        out += c
        i += 1
    return bytes(out)


def _runs(content: bytes, fonts: Dict[str, Font]) -> List[Tuple[float, float, str]]:
    runs: List[Tuple[float, float, str]] = []
    stack: List = []
    font: Optional[Font] = None
    size = 10.0
    leading = 0.0
    tx = ty = 0.0      # text line matrix origin
    sx = sy = 1.0      # text matrix scale (Tm a, d); rotation is ignored
    cx = 0.0           # current x advance estimate within the line
    for m in _TOKEN.finditer(content):
        tok = m.group(0)
        if tok.startswith(b"(") or tok.startswith(b"<") and not tok.startswith(b"<<"):
            stack.append(tok)
            continue
        if tok in (b"[", b"]") or tok.startswith(b"/") or re.match(rb"[+-]?[\d.]", tok):
            stack.append(tok)
            continue
        op = tok
        try:
            if op == b"BT":
                tx = ty = cx = 0.0
                sx = sy = 1.0
            elif op == b"Tf" and len(stack) >= 2:
                name = stack[-2].lstrip(b"/").decode("latin-1")
                font = fonts.get(name)
                size = abs(float(stack[-1])) or size
            elif op == b"TL" and stack:
                leading = float(stack[-1])
            elif op in (b"Td", b"TD") and len(stack) >= 2:
                dx, dy = float(stack[-2]), float(stack[-1])
                tx += dx * sx
                ty += dy * sy
                cx = 0.0
                if op == b"TD":
                    leading = -dy
            elif op == b"Tm" and len(stack) >= 6:
                sx, sy = float(stack[-6]) or 1.0, float(stack[-3]) or 1.0
                tx, ty = float(stack[-2]), float(stack[-1])
                cx = 0.0
            elif op == b"T*":
                ty -= leading * sy
                cx = 0.0
            elif op in (b"Tj", b"'", b'"', b"TJ"):
                if op in (b"'", b'"'):
                    ty -= leading * sy
                    cx = 0.0
                text = ""
                if op == b"TJ":
                    if b"[" in stack:
                        k = len(stack) - 1 - stack[::-1].index(b"[")
                        items = stack[k + 1:]
                    else:
                        items = stack[:]
                    for it in items:
                        if it.startswith(b"(") or (it.startswith(b"<") and not it.startswith(b"<<")):
                            text += _decode_str(it, font)
                        elif it != b"]":
                            try:
                                # a large negative kern is a visual space
                                if float(it) < -200:
                                    text += " "
                            except ValueError:
                                pass
                else:
                    strs = [s for s in stack if s.startswith(b"(") or s.startswith(b"<")]
                    if strs:
                        text = _decode_str(strs[-1], font)
                if text:
                    runs.append((tx + cx, ty, text))
                    cx += len(text) * size * abs(sx) * 0.5  # rough advance, only to order runs on one line
        except (ValueError, IndexError):
            pass
        stack = []
    return runs


def _decode_str(tok: bytes, font: Optional[Font]) -> str:
    if tok.startswith(b"("):
        raw = _literal(tok)
    else:
        h = re.sub(rb"[\s<>]", b"", tok)
        if len(h) % 2:
            h += b"0"
        raw = bytes.fromhex(h.decode("ascii"))
    if font is None:
        return raw.decode("cp1252", errors="replace")
    return font.decode(raw)


def _lines(runs: List[Tuple[float, float, str]]) -> List[str]:
    if not runs:
        return []
    ordered = sorted(runs, key=lambda r: (-r[1], r[0]))
    lines: List[List[Tuple[float, float, str]]] = []
    for r in ordered:
        if lines and abs(lines[-1][0][1] - r[1]) <= LINE_Y_TOLERANCE:
            lines[-1].append(r)
        else:
            lines.append([r])
    out = []
    for ln in lines:
        ln.sort(key=lambda r: r[0])
        parts: List[str] = []
        for x, _y, t in ln:
            if parts and not parts[-1].endswith(" ") and not t.startswith(" "):
                parts.append(" ")
            parts.append(t)
        s = " ".join("".join(parts).split())
        if s:
            out.append(s)
    return out


def extract_lines(data: bytes) -> List[List[str]]:
    """PDF bytes -> list of pages, each a list of text lines (top to bottom)."""
    doc = PDFDoc(data)
    pages = []
    for pnum in doc.pages():
        res = doc.page_resources(pnum)
        fonts = doc.fonts(res)
        content = doc.page_contents(pnum)
        runs = _runs(content, fonts)
        # Form XObjects drawn on the page (tables are sometimes inside one);
        # their own matrix is ignored, which is fine for line grouping.
        xo = doc.resolve(PDFDoc.dict_value(res, b"XObject") or b"")
        for m in re.finditer(rb"/([^\s/<>\[\]()]+)\s*(\d+)\s+\d+\s+R", xo):
            onum = int(m.group(2))
            d = doc.objs.get(onum, b"")
            if b"/Form" in d and re.search(rb"/" + re.escape(m.group(1)) + rb"\s+Do\b", content):
                ires = doc.resolve(PDFDoc.dict_value(d, b"Resources") or b"")
                f2 = dict(fonts)
                f2.update(doc.fonts(ires))
                runs += _runs(doc.stream(onum) or b"", f2)
        pages.append(_lines(runs))
    return pages


def extract_text(data: bytes) -> str:
    return "\n".join("\n".join(p) for p in extract_lines(data))
