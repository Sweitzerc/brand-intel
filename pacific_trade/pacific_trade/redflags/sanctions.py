"""Sanctions and export-control evasion checks, plus the watchlist tooling they share.

Watchlists are normalised into one schema from OFAC's SDN file, OpenSanctions'
``targets.simple.csv`` (EU, UK, UN and many more lists; free for non-commercial
use, commercial use needs a licence), the US Consolidated Screening List
(includes the BIS Entity List), or any CSV you add, such as the DHS UFLPA
Entity List.

The AIS checks follow the practices named in the 2020 OFAC / State / US Coast
Guard "Sanctions Advisory for the Maritime Industry": disabling AIS, falsifying
vessel identity, and frequent flag or name changes.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..crosswalk import haversine_km
from .framework import Context, make_flags, typology

OFAC_MARITIME = "OFAC/State/USCG Sanctions Advisory for the Maritime Industry (May 2020)"
OFAC_SDN_URLS = ["https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
                 "https://www.treasury.gov/ofac/downloads/sdn.csv"]
OPENSANCTIONS_URL = "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"

LIST_COLUMNS = ["list_source", "list_id", "schema", "name", "name_norm", "aliases_norm", "imo", "mmsi",
                "programs", "countries"]

LEGAL_SUFFIXES = {"LTD", "LIMITED", "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED", "LLC", "LLP",
                  "PLC", "SA", "SAS", "SRL", "BV", "NV", "GMBH", "AG", "PTE", "PVT", "PTY", "JSC", "OJSC", "PJSC",
                  "OOO", "ZAO", "FZE", "FZCO", "FZC", "DMCC", "SDN", "BHD", "KK", "GROUP", "HOLDING", "HOLDINGS",
                  "INTERNATIONAL", "INTL", "THE", "AND", "OF", "MV", "M/V"}


# ---------------------------------------------------------------- identifiers & names
def imo_valid(imo) -> bool:
    """IMO numbers carry a check digit: sum(d_i * (7..2)) mod 10 == last digit."""
    s = re.sub(r"\D", "", str(imo))
    if len(s) != 7:
        return False
    return sum(int(d) * w for d, w in zip(s[:6], range(7, 1, -1))) % 10 == int(s[6])


def extract_imos(text) -> list[str]:
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return []
    found = re.findall(r"(?<!\d)(\d{7})(?!\d)", str(text))
    return [f for f in dict.fromkeys(found) if imo_valid(f)]


def mid_valid(mmsi) -> bool:
    """Ship MMSIs are 9 digits whose first three (the MID, flag state) fall in 201-775."""
    s = str(mmsi)
    return len(s) == 9 and s.isdigit() and 201 <= int(s[:3]) <= 775


def norm_name(name) -> str:
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return ""
    s = re.sub(r"[^A-Z0-9 ]+", " ", str(name).upper())
    toks = [t for t in s.split() if t not in LEGAL_SUFFIXES]
    return " ".join(toks)


# ---------------------------------------------------------------- list loaders
def _finish(df: pd.DataFrame) -> pd.DataFrame:
    for c in LIST_COLUMNS:
        if c not in df:
            df[c] = ""
    df["name_norm"] = df["name"].map(norm_name)
    return df[LIST_COLUMNS].fillna("")


def load_ofac_sdn(path: Path) -> pd.DataFrame:
    cols = ["ent_num", "SDN_Name", "SDN_Type", "Program", "Title", "Call_Sign", "Vess_type", "Tonnage", "GRT",
            "Vess_flag", "Vess_owner", "Remarks"]
    raw = pd.read_csv(path, header=None, names=cols, dtype=str, encoding="latin-1").replace("-0- ", "")
    raw = raw.apply(lambda s: s.str.strip())
    df = pd.DataFrame({
        "list_source": "OFAC SDN", "list_id": raw["ent_num"], "name": raw["SDN_Name"],
        "schema": raw["SDN_Type"].fillna("").str.lower().map(
            {"vessel": "Vessel", "individual": "Person", "entity": "Organization", "aircraft": "Aircraft"}).fillna("Organization"),
        "imo": raw["Remarks"].map(lambda r: ";".join(extract_imos(r))),
        "programs": raw["Program"], "countries": raw["Vess_flag"],
    })
    return _finish(df)


def load_opensanctions(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    aliases = raw.get("aliases", pd.Series("", index=raw.index)).fillna("")
    df = pd.DataFrame({
        "list_source": "OpenSanctions:" + raw.get("datasets", raw.get("dataset", pd.Series("sanctions", index=raw.index))).fillna(""),
        "list_id": raw["id"], "schema": raw["schema"], "name": raw["name"],
        "aliases_norm": aliases.map(lambda a: ";".join(norm_name(x) for x in str(a).split(";") if x.strip())),
        "imo": [";".join(extract_imos(i)) if s == "Vessel" else "" for s, i in zip(raw["schema"], raw.get("identifiers", ""))],
        "programs": raw.get("sanctions", ""), "countries": raw.get("countries", ""),
    })
    return _finish(df)


def load_csl(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    typ = raw.get("type", pd.Series("", index=raw.index)).fillna("").str.lower()
    alt = raw.get("alt_names", pd.Series("", index=raw.index)).fillna("")
    text = raw.get("ids", pd.Series("", index=raw.index)).fillna("") + " " + raw.get("remarks", pd.Series("", index=raw.index)).fillna("")
    df = pd.DataFrame({
        "list_source": "CSL:" + raw.get("source", pd.Series("", index=raw.index)).fillna(""),
        "list_id": raw.get("_id", raw.get("entity_number", pd.Series("", index=raw.index))), "name": raw["name"],
        "schema": np.where(typ.str.contains("vessel"), "Vessel", np.where(typ.str.contains("individual"), "Person", "Organization")),
        "aliases_norm": alt.map(lambda a: ";".join(norm_name(x) for x in re.split(r"[;|]", str(a)) if x.strip())),
        "imo": [";".join(extract_imos(t)) if "vessel" in ty else "" for ty, t in zip(typ, text)],
        "programs": raw.get("programs", ""), "countries": raw.get("vessel_flag", ""),
    })
    return _finish(df)


def load_custom(path: Path, source: str) -> pd.DataFrame:
    """Any CSV with at least a ``name`` column (optional: schema, imo, mmsi, programs, countries, aliases)."""
    raw = pd.read_csv(path, dtype=str)
    raw.columns = [c.lower().strip() for c in raw.columns]
    if "name" not in raw:
        raise ValueError(f"{path} needs a 'name' column")
    df = pd.DataFrame({"list_source": source, "list_id": raw.get("id", pd.Series(raw.index.astype(str), index=raw.index)),
                       "name": raw["name"], "schema": raw.get("schema", "Organization"),
                       "aliases_norm": raw.get("aliases", pd.Series("", index=raw.index)).fillna("").map(
                           lambda a: ";".join(norm_name(x) for x in str(a).split(";") if x.strip())),
                       "imo": raw.get("imo", ""), "mmsi": raw.get("mmsi", ""),
                       "programs": raw.get("programs", ""), "countries": raw.get("countries", "")})
    return _finish(df)


def load_watchlists(ref_dir: Path) -> pd.DataFrame | None:
    """Load every list present under ``{data}/ref/lists``; custom CSVs go in ``lists/custom/``."""
    d = ref_dir / "lists"
    frames = []
    if (d / "ofac_sdn.csv").exists():
        frames.append(load_ofac_sdn(d / "ofac_sdn.csv"))
    if (d / "opensanctions.csv").exists():
        frames.append(load_opensanctions(d / "opensanctions.csv"))
    if (d / "csl.csv").exists():
        frames.append(load_csl(d / "csl.csv"))
    for p in sorted((d / "custom").glob("*.csv")) if (d / "custom").exists() else []:
        frames.append(load_custom(p, source=p.stem))
    return pd.concat(frames, ignore_index=True) if frames else None


# ---------------------------------------------------------------- name screening
def screen_names(names: pd.DataFrame, lists: pd.DataFrame, *, threshold: float = 0.9,
                 name_col: str = "name") -> pd.DataFrame:
    """Fuzzy-screen arbitrary entity names (companies, owners, importers) against watchlists.

    Exact matches on the normalised name or an alias score 1.0; otherwise candidates
    sharing a token are compared with difflib and kept at ``threshold`` or above.
    Every hit needs human review: common names collide.
    """
    lists = lists[lists["name_norm"] != ""]
    index: dict[str, list[int]] = {}
    keys = []
    for i, (n, a) in enumerate(zip(lists["name_norm"], lists["aliases_norm"])):
        variants = [n] + [x for x in str(a).split(";") if x]
        keys.append(variants)
        for v in variants:
            for tok in v.split():
                if len(tok) > 2:
                    index.setdefault(tok, []).append(i)
    out = []
    for q_raw in names[name_col].dropna().unique():
        q = norm_name(q_raw)
        if not q:
            continue
        cands = {i for tok in q.split() if len(tok) > 2 for i in index.get(tok, [])}
        for i in cands:
            best = max(1.0 if v == q else difflib.SequenceMatcher(None, q, v).ratio() for v in keys[i])
            if best >= threshold:
                row = lists.iloc[i]
                out.append({"query": q_raw, "match_name": row["name"], "list_source": row["list_source"],
                            "list_id": row["list_id"], "schema": row["schema"], "programs": row["programs"],
                            "match_score": round(best, 3)})
    return pd.DataFrame(out, columns=["query", "match_name", "list_source", "list_id", "schema", "programs", "match_score"])


# ---------------------------------------------------------------- AIS-derived inputs
def identity_history(points: pd.DataFrame) -> pd.DataFrame:
    """Per vessel (IMO where known, else MMSI): how many MMSIs, flags (MIDs), names and call signs it used."""
    p = points.dropna(subset=["mmsi"]).copy()
    p["mmsi_s"] = p["mmsi"].astype("Int64").astype(str)
    imo_by_mmsi = p.dropna(subset=["imo"]).groupby("mmsi_s")["imo"].agg(lambda s: s.mode().iloc[0])
    p["imo_r"] = p["imo"].fillna(p["mmsi_s"].map(imo_by_mmsi))
    p["key"] = np.where(p["imo_r"].notna(), "IMO" + p["imo_r"].astype("Int64").astype(str), "MMSI" + p["mmsi_s"])
    p["mid"] = p["mmsi_s"].str[:3]

    def uniq(s):
        return sorted({str(x) for x in s.dropna() if str(x).strip()})

    g = p.groupby("key")
    out = g.agg(imo=("imo_r", "first"), mmsis=("mmsi_s", uniq), mids=("mid", uniq), names=("vessel_name", uniq),
                call_signs=("call_sign", uniq), imos_seen=("imo", uniq),
                first_seen=("base_date_time", "min"), last_seen=("base_date_time", "max")).reset_index()
    out = out.rename(columns={"key": "vessel_key"})
    for c in ("mmsis", "mids", "names", "call_signs", "imos_seen"):
        out["n_" + c] = out[c].map(len)
        out[c] = out[c].map(";".join)
    # MMSIs that broadcast more than one IMO (identity sharing or spoofing).
    multi = p.dropna(subset=["imo"]).groupby("mmsi_s")["imo"].nunique()
    shared = set(multi[multi > 1].index)
    out["mmsi_multi_imo"] = out["mmsis"].map(lambda m: any(x in shared for x in m.split(";")))
    return out


def dark_gaps(points: pd.DataFrame, ports: pd.DataFrame, *, min_gap_h: float = 12.0, coverage_km: float = 30.0,
              min_move_km: float = 5.0) -> pd.DataFrame:
    """AIS silences that start and end well inside receiver coverage, with the ship having moved meanwhile.

    Both ends must lie within ``coverage_km`` of a port in the crosswalk, where
    terrestrial receivers reliably hear ships, so a silence there is less likely to be
    a coverage hole than one at the edge of range.
    """
    p = points.sort_values(["mmsi", "base_date_time"])[["mmsi", "base_date_time", "latitude", "longitude"]].copy()
    near = haversine_km(p["latitude"].to_numpy()[:, None], p["longitude"].to_numpy()[:, None],
                        ports["lat"].to_numpy()[None, :], ports["lon"].to_numpy()[None, :])
    p["near_km"] = near.min(axis=1)
    p["port"] = ports["geofence_id"].to_numpy()[near.argmin(axis=1)]
    nxt = p.groupby("mmsi").shift(-1)
    gap_h = (nxt["base_date_time"] - p["base_date_time"]).dt.total_seconds() / 3600
    move = haversine_km(p["latitude"], p["longitude"], nxt["latitude"], nxt["longitude"])
    m = (gap_h >= min_gap_h) & (p["near_km"] <= coverage_km) & (nxt["near_km"] <= coverage_km) & (move >= min_move_km)
    g = p[m].assign(gap_end=nxt.loc[m, "base_date_time"], gap_h=gap_h[m], moved_km=move[m], end_port=nxt.loc[m, "port"])
    return g.rename(columns={"base_date_time": "gap_start", "port": "start_port"})[
        ["mmsi", "gap_start", "gap_end", "gap_h", "moved_km", "start_port", "end_port"]].reset_index(drop=True)


# ---------------------------------------------------------------- typologies
@typology(
    id="SANC-VESSEL", area="sanctions", name="Vessel on a sanctions or export-control list",
    question="Did a ship seen in US coastal AIS match a listed vessel?",
    method=("IMO (check-digit validated) or MMSI exact match between the AIS vessel registry and normalised "
            "watchlists scores 0.95. A vessel-name match with no identifier match scores 0.5 for review."),
    sources=("OFAC SDN List", "OpenSanctions consolidated sanctions data", "US Consolidated Screening List (incl. BIS Entity List)",
             OFAC_MARITIME),
    data_needed=("vessel_registry", "watchlists (lists-fetch)"),
    innocent_explanations=("Delisted or licensed vessels still on an older list copy", "Ship names are reused; name-only matches are weak",
                           "IMO typos in AIS static data"),
)
def vessel_matches(ctx: Context) -> pd.DataFrame | None:
    if ctx.registry is None or ctx.watchlists is None:
        return None
    wl = ctx.watchlists
    by_imo = {}
    for r in wl[wl["imo"] != ""].itertuples():
        for i in str(r.imo).split(";"):
            by_imo.setdefault(i, r)
    by_mmsi = {str(r.mmsi): r for r in wl[wl["mmsi"] != ""].itertuples()}
    vessels = wl[wl["schema"] == "Vessel"]
    by_name = {}
    for r in vessels.itertuples():
        if r.name_norm:
            by_name.setdefault(r.name_norm, r)
    rows = []
    for v in ctx.registry.itertuples():
        imo = None if pd.isna(v.imo) else str(int(v.imo))
        hit, how, score = None, "", 0.0
        if imo and imo in by_imo:
            hit, how, score = by_imo[imo], f"IMO {imo}", 0.95
        elif str(v.mmsi) in by_mmsi:
            hit, how, score = by_mmsi[str(v.mmsi)], f"MMSI {v.mmsi}", 0.95
        elif getattr(v, "vessel_name", None) and norm_name(v.vessel_name) in by_name:
            hit, how, score = by_name[norm_name(v.vessel_name)], "name only", 0.5
        if hit is not None:
            rows.append({"vessel_key": v.vessel_key, "label": getattr(v, "vessel_name", None) or v.vessel_key, "how": how,
                         "score": score, "list": hit.list_source, "listed_name": hit.name, "programs": hit.programs})
    hits = pd.DataFrame(rows)
    if hits.empty:
        return hits
    return make_flags("SANC-VESSEL", hits, entity_type="vessel", entity_id="vessel_key", entity_label="label",
                      period="AIS period", score="score",
                      summary=lambda h: "Matches " + h["list"] + " entry (" + h["how"] + ")",
                      evidence=lambda h: "Listed as " + h["listed_name"] + "; programmes: " + h["programs"].astype(str),
                      caveats="Confirm against the official list and vessel registry (e.g. Equasis) before acting.")


@typology(
    id="SANC-IDENTITY", area="sanctions", name="Vessel identity manipulation",
    question="Does a ship's AIS identity look falsified, shared or frequently changed?",
    method=("From AIS static data over the period: an MMSI broadcasting several IMOs (0.6), an IMO failing its check "
            "digit (0.5), a change of flag (MMSI country prefix) for one IMO (0.5), three or more names (0.45), "
            "an MMSI outside the ship MID range 201-775 (0.4). The highest applicable score is used."),
    sources=(OFAC_MARITIME, "IMO ship identification number scheme (check digit)", "ITU MMSI / MID allocation"),
    data_needed=("vessel_identity (from portcalls)",),
    innocent_explanations=("Crew data-entry errors in AIS static messages", "Legitimate sale and re-flagging",
                           "Shared or default MMSIs on small craft", "Receiver decoding errors"),
)
def identity_flags(ctx: Context) -> pd.DataFrame | None:
    if ctx.identity is None:
        return None
    d = ctx.identity.copy()
    checks = [
        (d["mmsi_multi_imo"].astype(bool), 0.6, "MMSI broadcast more than one IMO"),
        (d["imo"].notna() & ~d["imo"].map(lambda x: pd.isna(x) or imo_valid(int(x))), 0.5, "IMO fails check digit"),
        (d["n_mids"] > 1, 0.5, "Flag (MMSI country prefix) changed"),
        (d["n_names"] >= 3, 0.45, "Three or more names"),
        (~d["mmsis"].map(lambda m: all(mid_valid(x) for x in m.split(";") if x)), 0.4, "MMSI outside ship MID range"),
    ]
    d["score"] = 0.0
    d["reasons"] = ""
    for mask, s, text in checks:
        mask = mask.fillna(False).astype(bool)
        d.loc[mask, "score"] = np.maximum(d.loc[mask, "score"], s)
        d.loc[mask, "reasons"] = d.loc[mask, "reasons"] + np.where(d.loc[mask, "reasons"] == "", "", "; ") + text
    hits = d[d["score"] > 0]
    return make_flags("SANC-IDENTITY", hits, entity_type="vessel", entity_id="vessel_key",
                      entity_label=lambda h: h["names"].str.split(";").str[0].replace("", np.nan).fillna(h["vessel_key"]),
                      period=lambda h: h["first_seen"].astype(str).str[:10] + ".." + h["last_seen"].astype(str).str[:10],
                      score="score", summary="reasons",
                      evidence=lambda h: "MMSIs " + h["mmsis"] + " · names " + h["names"] + " · call signs " + h["call_signs"],
                      caveats="AIS static data is crew-entered; corroborate with a ship registry before drawing conclusions.")


@typology(
    id="SANC-DARK", area="sanctions", name="AIS dark period inside coverage",
    question="Did a ship go silent where receivers should hear it, and reappear somewhere else?",
    method=("Gaps of at least 12 h between consecutive AIS positions where both ends are within 30 km of a crosswalk "
            "port and the ship moved at least 5 km. Score 0.35 rising to 0.75 for gaps of 72 h or more."),
    sources=(OFAC_MARITIME, "Global Fishing Watch research on AIS disabling"),
    data_needed=("ais_gaps (from portcalls)",),
    innocent_explanations=("Receiver or data-pipeline outages", "AIS switched off at berth for maintenance",
                           "Daily-file boundaries if days are missing from the AIS pull"),
)
def dark_flags(ctx: Context) -> pd.DataFrame | None:
    if ctx.gaps is None or ctx.registry is None:
        return None
    g = ctx.gaps.merge(ctx.registry[["mmsi", "vessel_key"] + (["vessel_name"] if "vessel_name" in ctx.registry else [])],
                       on="mmsi", how="left")
    if g.empty:
        return g
    g["vessel_key"] = g["vessel_key"].fillna("MMSI" + g["mmsi"].astype(str))
    score = 0.35 + 0.4 * np.minimum(1, (g["gap_h"] - 12) / 60)
    return make_flags("SANC-DARK", g, entity_type="vessel", entity_id="vessel_key",
                      entity_label=lambda h: h.get("vessel_name", h["vessel_key"]).fillna(h["vessel_key"]),
                      period=lambda h: h["gap_start"].astype(str).str[:16], score=score,
                      summary=lambda h: "Silent " + h["gap_h"].round(0).astype(int).astype(str) + " h near " + h["start_port"] + " → " + h["end_port"],
                      evidence=lambda h: [f"{s} to {e}; moved {k:.0f} km while silent" for s, e, k in
                                          zip(h["gap_start"].astype(str).str[:16], h["gap_end"].astype(str).str[:16], h["moved_km"])],
                      caveats="Check for missing AIS days and receiver outages before treating as deliberate.")


@typology(
    id="SANC-COUNTRY", area="sanctions", name="Imports from a high-risk jurisdiction",
    question="Do in-scope goods arrive from a country under comprehensive sanctions, on a FATF list, or with a high AML risk score?",
    method=("Joins Census origins to your country_risk.csv: comprehensive sanctions 0.9, FATF call for action "
            "('black') 0.8, FATF increased monitoring ('grey') 0.45, Basel AML Index score of 6.5 or more 0.4."),
    sources=("FATF high-risk and other monitored jurisdictions", "Basel Institute on Governance, Basel AML Index",
             "OFAC sanctions programmes"),
    data_needed=("census_porths", "crosswalks/country_risk.csv (you fill it from the current lists)"),
    innocent_explanations=("Licensed or exempt trade", "Country risk describes the jurisdiction, not the trader"),
)
def country_flags(ctx: Context) -> pd.DataFrame | None:
    if ctx.census is None or ctx.country_risk is None or ctx.country_risk.empty:
        return None
    r = ctx.country_risk.copy()
    r["score"] = 0.0
    r["why"] = ""
    rules = [(r["comprehensive_sanctions"].astype(str).isin(["1", "True", "true", "yes"]), 0.9, "comprehensive sanctions"),
             (r["fatf_status"].astype(str).str.lower() == "black", 0.8, "FATF call for action"),
             (r["fatf_status"].astype(str).str.lower() == "grey", 0.45, "FATF increased monitoring"),
             (pd.to_numeric(r["basel_aml_score"], errors="coerce") >= 6.5, 0.4, "Basel AML Index ≥ 6.5")]
    for mask, s, text in rules:
        r.loc[mask, "score"] = np.maximum(r.loc[mask, "score"], s)
        r.loc[mask, "why"] = r.loc[mask, "why"] + np.where(r.loc[mask, "why"] == "", "", "; ") + text
    r = r[r["score"] > 0]
    c = ctx.census[ctx.census["is_country"]].merge(ctx.countries[["cty_code", "iso3", "m49_comtrade", "name"]],
                                                    left_on="CTY_CODE", right_on="cty_code")
    c = c.groupby(["iso3", "m49_comtrade", "name"], as_index=False)["GEN_VAL_MO"].sum()
    hits = c.merge(r[["iso3", "score", "why", "as_of"]], on="iso3")
    return make_flags("SANC-COUNTRY", hits, entity_type="origin_country", entity_id=lambda h: "M49:" + h["m49_comtrade"].astype(str),
                      entity_label="name", period="all months", score="score",
                      summary=lambda h: "Origin rated: " + h["why"],
                      evidence=lambda h: [f"${v:,.0f} of in-scope imports; ratings as of {a}" for v, a in zip(h["GEN_VAL_MO"], h["as_of"])],
                      caveats="Ratings come from your country_risk.csv; keep it current with the latest FATF plenary and Basel AML Index.")
