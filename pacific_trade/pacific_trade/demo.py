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
                    price = usd_kg * rng.lognormal(0, 0.3)
                    rows.append([port, m, hs6, cty, gen, ves, ves / price, air, ves * 0.6, air / (price * 3)])
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
        "imo": pd.array([_valid_imo(x) for x in 900_000 + rng.choice(99_999, n, replace=False)], dtype="Int64"),
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


def _world(rng, countries, cen):
    """World exports by reporter; every origin seen in the demo Census reports comfortably more than the US
    takes, except one planted lane where the US imports exceed the origin's total exports."""
    m49 = countries["m49_comtrade"].tolist() + [842]
    us = cen[cen["is_country"]].merge(countries[["cty_code", "m49_comtrade"]], left_on="CTY_CODE", right_on="cty_code")
    us = us.groupby(["hs6", "m49_comtrade"])["GEN_VAL_MO"].sum()
    planted = us[us > 2e6].sample(1, random_state=int(rng.integers(1e6))).index[0]
    rows = []
    for hs6 in HS6:
        rep = list(dict.fromkeys(list(rng.choice(m49, size=14, replace=False)) +
                                 [m for h, m in us.index if h == hs6]))
        alpha = np.full(len(rep), 0.5)
        if hs6 == "284690":
            rep[0], alpha[0] = 156, 12
        if hs6.startswith("8542"):
            rep[0], alpha[0] = 490, 4
        for r, v in zip(rep, rng.dirichlet(alpha) * rng.lognormal(23, 0.6)):
            took = us.get((hs6, r), 0)
            v = took / 4 if (hs6, r) == planted else max(v, took * rng.uniform(3, 20))
            rows.append({"cmdCode": hs6, "refYear": 2026, "reporterCode": int(r), "partnerCode": 0,
                         "primaryValue": float(v), "isAggregate": False})
    return pd.DataFrame(rows)


def _valid_imo(first6: int) -> int:
    d = str(first6)
    return int(d + str(sum(int(x) * w for x, w in zip(d, range(7, 1, -1))) % 10))


# ---------------------------------------------------------------- planted anomalies for the red-flag demo
def _plant_trade_anomalies(rng, cen: pd.DataFrame) -> pd.DataFrame:
    cen = cen.copy()
    big = cen[cen["is_country"] & (cen["VES_VAL_MO"] > 2e5)].index
    over, under = rng.choice(big, 3, replace=False), rng.choice(big, 2, replace=False)
    cen.loc[over, "VES_WGT_MO"] = (cen.loc[over, "VES_WGT_MO"] / 300).round()      # same value, far less weight
    cen.loc[under, "VES_WGT_MO"] = cen.loc[under, "VES_WGT_MO"] * 150               # same value, far more weight
    # A minor producer (Laos) suddenly ships a big share of one IC line in the last three months.
    tmpl = cen[(cen["PORT"] == "2709") & (cen["hs6"] == "854231") & cen["is_country"]].drop_duplicates("month")
    surge = tmpl[tmpl["month"].isin(MONTHS[-3:])].copy()
    surge[["CTY_CODE", "CTY_NAME"]] = ["5530", "LAOS"]
    for c in ("GEN_VAL_MO", "VES_VAL_MO", "VES_WGT_MO", "AIR_VAL_MO", "CNT_VAL_MO", "CNT_WGT_MO", "AIR_WGT_MO"):
        surge[c] = surge[c] * 6
    return pd.concat([cen, surge], ignore_index=True)


def _mirror(rng, cen: pd.DataFrame, countries: pd.DataFrame):
    c = cen[cen["is_country"]].groupby(["CTY_CODE", "hs6"], as_index=False)["GEN_VAL_MO"].sum()
    c = c.merge(countries[["cty_code", "m49_comtrade"]], left_on="CTY_CODE", right_on="cty_code")
    c["annual"] = c["GEN_VAL_MO"] * 2      # six months -> a year
    gap = np.ones(len(c))
    pick = rng.choice(len(c), 4, replace=False)
    gap[pick] = [3.2, 0.25, 2.4, 0.4]
    us = pd.DataFrame({"cmdCode": c["hs6"], "period": "2026", "partnerCode": c["m49_comtrade"].astype(int),
                       "primaryValue": c["annual"], "reporterCode": 842, "flowCode": "M"})
    px = pd.DataFrame({"cmdCode": c["hs6"], "period": "2026", "reporterCode": c["m49_comtrade"].astype(int),
                       "primaryValue": c["annual"] / 1.05 / gap * rng.lognormal(0, 0.08, len(c)),
                       "partnerCode": 842, "flowCode": "X"})
    return us, px


def _ais_red_flags(rng, reg: pd.DataFrame):
    ident = pd.DataFrame({
        "vessel_key": reg["vessel_key"], "imo": reg["imo"], "mmsis": reg["mmsi"].astype(str),
        "mids": reg["mmsi"].astype(str).str[:3], "names": reg["vessel_name"], "call_signs": "DEMO",
        "imos_seen": reg["imo"].astype(str), "first_seen": pd.Timestamp("2026-01-01", tz="UTC"),
        "last_seen": pd.Timestamp("2026-06-30", tz="UTC"), "mmsi_multi_imo": False})
    for c in ("mmsis", "mids", "names", "call_signs", "imos_seen"):
        ident["n_" + c] = 1
    odd = rng.choice(len(ident), 6, replace=False)
    ident.loc[odd[:2], "n_mids"] = 2
    ident.loc[odd[:2], "mids"] = "351;667"
    ident.loc[odd[2:4], "mmsi_multi_imo"] = True
    ident.loc[odd[4:], "n_names"] = 3
    ident.loc[odd[4:], "names"] = ident.loc[odd[4:], "names"] + ";DEMO ALIAS A;DEMO ALIAS B"
    k = 6
    who = rng.choice(len(reg), k, replace=False)
    start = pd.Timestamp("2026-03-01", tz="UTC") + pd.to_timedelta(rng.uniform(0, 90 * 24, k), "h")
    hours = rng.uniform(14, 90, k)
    ports = rng.choice(list(PORT_SCALE), k)
    gaps = pd.DataFrame({"mmsi": reg["mmsi"].to_numpy()[who], "gap_start": start, "gap_end": start + pd.to_timedelta(hours, "h"),
                         "gap_h": hours, "moved_km": rng.uniform(6, 40, k), "start_port": ports, "end_port": ports})
    listed = rng.choice(len(reg), 2, replace=False)
    wl = pd.DataFrame({"list_source": "DEMO LIST (fictional)", "list_id": ["D1", "D2", "D3"], "schema": "Vessel",
                       "name": ["DEMO LISTED TANKER", "DEMO LISTED CARRIER", reg["vessel_name"].iloc[odd[0]]],
                       "imo": [str(reg["imo"].iloc[listed[0]]), str(reg["imo"].iloc[listed[1]]), ""],
                       "mmsi": "", "programs": "FICTIONAL-PROGRAMME", "countries": "", "aliases_norm": ""})
    from .redflags.sanctions import norm_name
    wl["name_norm"] = wl["name"].map(norm_name)
    return ident, gaps, wl


def demo_tables(seed: int = 7, method: str = "capacity") -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    countries = crosswalk.load_countries()
    cen = _plant_trade_anomalies(rng, _census(rng, countries))
    calls, reg, pm = _ais(rng)
    us, px = _mirror(rng, cen, countries)
    ident, gaps, wl = _ais_red_flags(rng, reg)
    reference = {"dol_goods": pd.read_csv(crosswalk.CROSSWALK_DIR / "dol_goods_hs.csv", dtype=str), "country_risk": None}
    return build_tables(cen, countries, port_month=pm, calls=calls, registry=reg, world=_world(rng, countries, cen),
                        us_imports=us, partner_exports=px, identity=ident, gaps=gaps, watchlists=wl,
                        reference=reference, method=method)
