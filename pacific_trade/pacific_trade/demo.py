"""SYNTHETIC inputs for previewing the dashboard and report layout.

Nothing here is real data. The values are random draws loosely shaped by what the
recon expects (chips mostly fly, ores ride ships, LA/Long Beach dwarf Hawaii), and
they go through the same build code as real pulls, so the demo exercises the real
metrics and allocation. Every output rendered from it is labelled as a demo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import census, crosswalk, portcalls
from .build import build_tables

MONTHS = [f"2026-{m:02d}" for m in range(1, 7)]
# hs6 -> (typical vessel share of value, USD per kg)
HS6 = {
    "854129": (0.15, 900), "854140": (0.35, 120), "854231": (0.04, 9000), "854232": (0.05, 6000),
    "854239": (0.06, 5000), "854370": (0.30, 150), "854442": (0.65, 18), "854449": (0.70, 12),
    "261100": (0.96, 30), "261000": (0.97, 0.6), "261400": (0.97, 1.8), "261590": (0.95, 25),
    "261690": (0.92, 40), "261790": (0.95, 8), "284690": (0.90, 35),
}
PORT_SCALE = {"2704": 40, "2709": 45, "2811": 12, "2809": 3, "3001": 10, "3002": 9,
              "3201": 2.0, "3202": 0.3, "3203": 0.4, "3204": 0.15, "3206": 0.1}


def _census(rng, countries) -> pd.DataFrame:
    codes = countries["cty_code"].tolist()
    asia = ["5830", "5800", "5880", "5700", "5570", "5520", "5650", "5490", "5590"]
    rows = []
    for port, scale in PORT_SCALE.items():
        for hs6, (ves_share, usd_kg) in HS6.items():
            pool = asia if hs6.startswith("85") else codes
            k = rng.integers(2, 7)
            origins = rng.choice(pool, size=k, replace=False)
            # REE compounds lean heavily on China.
            alpha = np.full(k, 0.6)
            if hs6 == "284690" and "5700" not in origins:
                origins[0] = "5700"
            if hs6 == "284690":
                alpha[list(origins).index("5700")] = 6
            mix = rng.dirichlet(alpha)
            base = scale * rng.lognormal(13, 0.8)
            for m in MONTHS:
                if scale < 0.5 and rng.random() < 0.35:
                    continue  # mimic disclosure suppression at small ports
                month_val = base * rng.lognormal(0, 0.25)
                for cty, w in zip(origins, mix):
                    gen = month_val * w
                    vs = np.clip(ves_share * rng.lognormal(0, 0.2), 0, 1)
                    ves = gen * vs
                    air = gen - ves
                    rows.append([port, m, hs6, cty, gen, ves, ves / usd_kg, air, ves * 0.6, air / (usd_kg * 3)])
    df = pd.DataFrame(rows, columns=["PORT", "time", "I_COMMODITY", "CTY_CODE", "GEN_VAL_MO", "VES_VAL_MO",
                                     "VES_WGT_MO", "AIR_VAL_MO", "CNT_VAL_MO", "AIR_WGT_MO"])
    df["CNT_WGT_MO"] = df["VES_WGT_MO"] * 0.6
    names = countries.set_index("cty_code")["name"]
    df["CTY_NAME"] = df["CTY_CODE"].map(names).str.upper()
    ports = crosswalk.load_ports().drop_duplicates("schedule_d").set_index("schedule_d")["port_name"]
    df["PORT_NAME"] = df["PORT"].map(ports)
    df[["GEN_VAL_MO", "VES_VAL_MO", "VES_WGT_MO", "AIR_VAL_MO", "CNT_VAL_MO", "AIR_WGT_MO", "CNT_WGT_MO"]] = \
        df[["GEN_VAL_MO", "VES_VAL_MO", "VES_WGT_MO", "AIR_VAL_MO", "CNT_VAL_MO", "AIR_WGT_MO", "CNT_WGT_MO"]].round()
    return census.tidy(df.astype(str))


def _ais(rng):
    n = 400
    reg = pd.DataFrame({
        "mmsi": pd.array(200_000_000 + rng.choice(500_000_000, n, replace=False), dtype="Int64"),
        "imo": pd.array(9_000_000 + rng.choice(999_999, n, replace=False), dtype="Int64"),
        "vessel_type": pd.array(np.where(rng.random(n) < 0.8, rng.integers(70, 80, n), rng.integers(80, 90, n)),
                                dtype="Int64"),
        "length": rng.uniform(90, 400, n).round(), "width": rng.uniform(15, 60, n).round(),
    })
    reg["vessel_key"] = "IMO" + reg["imo"].astype(str)
    reg["vessel_name"] = [f"DEMO VESSEL {i:03d}" for i in range(n)]
    calls = []
    for port, scale in PORT_SCALE.items():
        n_month = max(3, int(scale * 2.5))
        regulars = rng.choice(n, size=max(4, n_month // 2), replace=False)
        for m in MONTHS:
            k = max(1, int(n_month * rng.lognormal(0, 0.15)))
            # Small ports lean on a few regular liner services.
            idx = rng.choice(regulars, size=k) if scale < 1 else rng.choice(n, size=k)
            day = rng.integers(1, 28, size=k)
            arrival = pd.to_datetime([f"{m}-{d:02d}" for d in day], utc=True) + pd.to_timedelta(rng.uniform(0, 24, k), "h")
            dwell = rng.gamma(3, 8 if scale > 5 else 6, k)
            draft = rng.uniform(8, 15, k)
            calls.append(pd.DataFrame({
                "mmsi": reg["mmsi"].to_numpy()[idx], "geofence_id": port, "arrival": arrival,
                "departure": arrival + pd.to_timedelta(dwell * 1.2, "h"), "dwell_h": dwell,
                "arr_draft": draft, "dep_draft": draft - rng.uniform(-0.5, 2.5, k),
                "anchorage_wait_h": np.where(rng.random(k) < (0.5 if scale > 20 else 0.15),
                                             rng.gamma(2, 10, k), 0.0),
                "censored": False, "port_key": port}))
    calls = pd.concat(calls, ignore_index=True)
    return calls, reg, portcalls.port_month_stats(calls, reg)


def _world(rng, countries):
    m49 = countries["m49_comtrade"].tolist() + [842]
    rows = []
    for hs6 in HS6:
        k = 14
        rep = rng.choice(m49, size=k, replace=False)
        alpha = np.full(k, 0.5)
        if hs6 == "284690":
            rep[0], alpha[0] = 156, 12
        if hs6.startswith("8542"):
            rep[0], alpha[0] = 490, 4
        for r, v in zip(rep, rng.dirichlet(alpha) * rng.lognormal(23, 0.6)):
            rows.append({"cmdCode": hs6, "refYear": 2025, "reporterCode": int(r), "partnerCode": 0,
                         "primaryValue": float(v), "isAggregate": False})
    return pd.DataFrame(rows)


def demo_tables(seed: int = 7, method: str = "capacity") -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    countries = crosswalk.load_countries()
    cen = _census(rng, countries)
    calls, reg, pm = _ais(rng)
    return build_tables(cen, countries, port_month=pm, calls=calls, registry=reg,
                        world=_world(rng, countries), method=method)
