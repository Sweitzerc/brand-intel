"""UN Comtrade client (country-level context).

Keyed:   data/v1/get   - 500 calls/day, 1 call/s, up to 100k records/call (free tier).
Keyless: public/v1/preview - 500 records/call, one period & product per call.

Comtrade's sea/air split (``motCode``) came back as all-modes only for the US,
Taiwan and Korea in the recon, so this module always asks for motCode=0 and leaves
mode-of-transport to Census.
"""

from __future__ import annotations

import os
import time
from typing import Iterable

import pandas as pd

from . import http
from .config import COMTRADE_AVAIL, COMTRADE_DATA, COMTRADE_PREVIEW, data_dir

KEEP = ["freqCode", "period", "refYear", "refMonth", "reporterCode", "flowCode", "partnerCode",
        "partner2Code", "cmdCode", "motCode", "customsCode", "netWgt", "qty", "qtyUnitAbbr",
        "cifvalue", "fobvalue", "primaryValue", "isAggregate"]


class ComtradeError(RuntimeError):
    pass


class _Throttle:
    """Keep to 1 call/s and count calls against the free 500/day budget."""

    def __init__(self, min_interval=1.1, daily_budget=500):
        self.min_interval, self.daily_budget = min_interval, daily_budget
        self.last, self.calls = 0.0, 0

    def wait(self):
        if self.calls >= self.daily_budget:
            raise ComtradeError(f"Daily call budget ({self.daily_budget}) used up in this process")
        dt = time.monotonic() - self.last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self.last = time.monotonic()
        self.calls += 1


THROTTLE = _Throttle()


def _join(v) -> str:
    return ",".join(str(x) for x in v) if isinstance(v, (list, tuple, set)) else str(v)


def get(freq: str, *, cmd: Iterable[str] | str, period: Iterable[str] | str, flow: str,
        reporter: Iterable[int] | int | None = None, partner: Iterable[int] | int | None = None,
        key: str | None = None, use_cache: bool = True) -> pd.DataFrame:
    """Fetch Comtrade records. ``freq`` is 'A' or 'M'; periods are 'YYYY' or 'YYYYMM'.

    Leaving ``reporter`` as None asks for all reporters (keyed API only), which is
    what the world-exporter concentration metric needs.
    """
    key = key if key is not None else os.environ.get("COMTRADE_API_KEY", "").strip()
    params = {"cmdCode": _join(cmd), "period": _join(period), "flowCode": flow,
              "motCode": "0", "customsCode": "C00", "partner2Code": "0", "includeDesc": "false"}
    if reporter is not None:
        params["reporterCode"] = _join(reporter)
    if partner is not None:
        params["partnerCode"] = _join(partner)

    if key:
        url = COMTRADE_DATA.format(freq=freq)
    else:
        url = COMTRADE_PREVIEW.format(freq=freq)
        if "," in params["period"] or "," in params["cmdCode"] or reporter is None:
            raise ComtradeError("Keyless preview takes one period, one product and an explicit "
                                "reporter per call; set COMTRADE_API_KEY for batched pulls.")

    def fetch():
        THROTTLE.wait()
        headers = {"Ocp-Apim-Subscription-Key": key} if key else None
        r = http.get(url, params=params, headers=headers)
        if r.status_code != 200:
            raise ComtradeError(f"Comtrade {r.status_code}: {r.text[:400]}")
        body = r.json()
        if body.get("error"):
            raise ComtradeError(f"Comtrade error: {body['error']}")
        return body.get("data") or []

    rows = http.cached_json(data_dir() / "cache" / "comtrade", url, params, fetch) if use_cache else fetch()
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=KEEP)
    df = df[[c for c in KEEP if c in df.columns]].copy()
    for c in ("netWgt", "qty", "cifvalue", "fobvalue", "primaryValue"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["cmdCode"] = df["cmdCode"].astype(str)
    return df


def availability(freq: str, reporter: int, period: str) -> pd.DataFrame:
    r = http.get(COMTRADE_AVAIL.format(freq=freq), params={"reporterCode": reporter, "period": period})
    r.raise_for_status()
    return pd.DataFrame(r.json().get("data") or [])


def us_imports_by_partner(freq: str, cmd: list[str], periods: list[str]) -> pd.DataFrame:
    """US-reported imports by partner (the Census cross-check and US supplier view)."""
    from .config import COMTRADE_USA
    return get(freq, cmd=cmd, period=periods, flow="M", reporter=COMTRADE_USA)


def world_exports_by_reporter(year: str, cmd: list[str]) -> pd.DataFrame:
    """Every reporter's exports to World (partner 0): input to global exporter HHI."""
    df = get("A", cmd=cmd, period=year, flow="X", partner=0)
    return df[df["partnerCode"].astype(int) == 0]
