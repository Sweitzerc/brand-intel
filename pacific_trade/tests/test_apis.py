import pytest

from pacific_trade import census, comtrade, http


class FakeResp:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code, self._p, self.headers, self.text = status, payload, headers or {}, text

    def json(self):
        return self._p


def test_census_query_parses_and_caches(monkeypatch):
    calls = []
    payload = [["PORT", "I_COMMODITY", "CTY_CODE", "VES_VAL_MO", "time", "I_COMMODITY"],
               ["3201", "854231", "5830", "1000", "2026-06", "854231"],
               ["3201", "854231", "-", "1000", "2026-06", "854231"]]

    def fake_get(url, params=None, **kw):
        calls.append(params)
        return FakeResp(payload=payload)

    monkeypatch.setattr(http, "get", fake_get)
    q = dict(time="2026-06", COMM_LVL="HS6", I_COMMODITY="8542*")
    df = census.tidy(census.query("porths", ["PORT", "I_COMMODITY"], q, key="K"))
    census.query("porths", ["PORT", "I_COMMODITY"], q, key="OTHER")   # cache hit even with a new key
    assert len(calls) == 1 and calls[0]["key"] == "K"
    assert df["VES_VAL_MO"].dtype.kind == "i" or df["VES_VAL_MO"].dtype.kind == "f"
    assert df["is_country"].tolist() == [True, False]
    assert df["hs6"].tolist() == ["854231"] * 2 and df["month"].iloc[0] == "2026-06"


def test_census_missing_key_redirect(monkeypatch):
    monkeypatch.setattr(http, "get", lambda *a, **k: FakeResp(302, headers={"Location": "/data/missing_key.html"}))
    with pytest.raises(census.CensusError):
        census.query("porths", ["PORT"], {"time": "2026-06"}, key="bad", use_cache=False)


def test_census_requires_key_env():
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
        census.query("porths", ["PORT"], {"time": "2026-06"}, use_cache=False)


def test_time_predicate():
    assert census.time_predicate("2026-04", "2026-06") == "from 2026-04 to 2026-06"
    assert census.time_predicate("2026-06") == "2026-06"


def test_comtrade_keyed_call(monkeypatch):
    seen = {}
    comtrade.THROTTLE.min_interval = 0

    def fake_get(url, params=None, headers=None, **kw):
        seen.update(url=url, params=params, headers=headers)
        return FakeResp(payload={"data": [{"period": "202607", "refYear": 2026, "reporterCode": 842,
                                           "flowCode": "M", "partnerCode": 156, "cmdCode": 284690,
                                           "primaryValue": "13456650", "netWgt": 343919}]})

    monkeypatch.setattr(http, "get", fake_get)
    df = comtrade.get("M", cmd=["2846"], period=["202607"], flow="M", reporter=842, key="K")
    assert "data/v1/get/C/M/HS" in seen["url"] and seen["headers"]["Ocp-Apim-Subscription-Key"] == "K"
    assert df.loc[0, "primaryValue"] == 13456650 and df.loc[0, "cmdCode"] == "284690"


def test_comtrade_keyless_guards_batching():
    with pytest.raises(comtrade.ComtradeError):
        comtrade.get("A", cmd=["8541", "8542"], period="2025", flow="X", reporter=842, key="")
