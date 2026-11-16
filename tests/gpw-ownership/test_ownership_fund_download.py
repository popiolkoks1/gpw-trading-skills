"""Fund PDF download goes through gpwlib.http.get_bytes (no private _fetch); offline."""

from types import SimpleNamespace

import pytest

import gpw_ownership as cli
from gpwlib import gpw_archive, http

PDF = b"%PDF-1.4\n\x00\xff\x9c binary \r\n%%EOF"


class _Resp:
    def __init__(self, body):
        self._b, self.status, self.headers = body, 200, {"Content-Type": "application/pdf"}

    def read(self):
        return self._b

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(monkeypatch):
    calls = []
    body = {"v": PDF}

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _Resp(body["v"])

    monkeypatch.setattr(http, "_urlopen", fake_urlopen)
    monkeypatch.setattr(http.LIMITER, "wait", lambda host: 0.0)
    return calls, body


def _args(tmp_path, offline=False):
    return SimpleNamespace(cache_dir=str(tmp_path), offline=offline)


def test_fund_pdf_bytes_intact_and_cached(tmp_path, net):
    calls, _ = net
    assert cli._fetch_fund_pdf("NNOFE", 2025, _args(tmp_path)) == PDF
    assert calls == [cli.FUNDS_URL.format(code="NNOFE", date="2025-12-31")]
    assert cli._fetch_fund_pdf("NNOFE", 2025, _args(tmp_path, offline=True)) == PDF  # from the bytes cache
    assert len(calls) == 1


def test_fund_pdf_offline_miss_and_non_pdf(tmp_path, net):
    calls, body = net
    with pytest.raises(gpw_archive.GPWFetchError, match="not cached"):
        cli._fetch_fund_pdf("NNOFE", 2024, _args(tmp_path, offline=True))
    body["v"] = b"<html>not found</html>"
    with pytest.raises(gpw_archive.GPWFetchError, match="no PDF"):
        cli._fetch_fund_pdf("NNOFE", 2023, _args(tmp_path))
    body["v"] = PDF  # the error page was not kept in the cache
    assert cli._fetch_fund_pdf("NNOFE", 2023, _args(tmp_path)) == PDF and len(calls) == 2


def test_legacy_cached_copy_still_used(tmp_path, net):
    calls, _ = net
    legacy = tmp_path / "ownership" / "funds" / "NNOFE_2022.pdf"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"%PDF-legacy")
    assert cli._fetch_fund_pdf("NNOFE", 2022, _args(tmp_path)) == b"%PDF-legacy" and calls == []
