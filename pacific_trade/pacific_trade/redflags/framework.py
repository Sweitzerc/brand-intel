"""Typology registry, flag schema and entity risk roll-up.

Every red-flag check is registered as a ``Typology`` that documents what it tests,
where the pattern comes from (FATF, OFAC, DOL, ...), the data it needs, and its
known innocent explanations. Checks return rows in one shared flag schema so that
any number of modules can be added without touching the dashboard or report.

A flag is a lead for an analyst to review. It is not evidence that anyone broke
the law, and the output never says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

FLAG_COLUMNS = ["typology_id", "area", "entity_type", "entity_id", "entity_label", "period",
                "score", "severity", "summary", "evidence", "caveats", "hs6", "port"]

AREAS = {
    "tbml": "Trade-based money laundering",
    "sanctions": "Sanctions & export-control evasion",
    "forced_labor": "Forced labour in supply chains",
}


@dataclass(frozen=True)
class Typology:
    id: str
    area: str
    name: str
    question: str                      # what the check asks of the data
    method: str                        # how it scores
    sources: tuple[str, ...]           # where the pattern comes from
    data_needed: tuple[str, ...]       # output tables / reference lists it reads
    innocent_explanations: tuple[str, ...]
    run: Callable[["Context"], pd.DataFrame] = field(compare=False, repr=False)

    def info(self) -> dict:
        d = asdict(self)
        d.pop("run")
        d["area_name"] = AREAS.get(self.area, self.area)
        return d


REGISTRY: dict[str, Typology] = {}


def typology(**meta):
    """Decorator: register a check function as a typology."""
    def wrap(fn):
        t = Typology(run=fn, **meta)
        if t.id in REGISTRY:
            raise ValueError(f"duplicate typology id {t.id}")
        if t.area not in AREAS:
            raise ValueError(f"unknown area {t.area}")
        REGISTRY[t.id] = t
        return fn
    return wrap


@dataclass
class Context:
    """Everything a check may read. Missing inputs are None; checks skip themselves."""
    census: pd.DataFrame | None = None            # tidy Census porths
    countries: pd.DataFrame | None = None         # crosswalk with m49_comtrade / iso3
    world_exports: pd.DataFrame | None = None     # Comtrade reporter -> World exports
    us_imports: pd.DataFrame | None = None        # Comtrade, US-reported imports by partner
    partner_exports: pd.DataFrame | None = None   # Comtrade, partners' reported exports to the US
    registry: pd.DataFrame | None = None          # AIS vessel registry (one row per MMSI)
    identity: pd.DataFrame | None = None          # AIS identity history (one row per IMO)
    gaps: pd.DataFrame | None = None              # AIS dark gaps inside coverage
    watchlists: pd.DataFrame | None = None        # normalised sanctions / export-control lists
    dol_goods: pd.DataFrame | None = None         # DOL TVPRA goods mapped to HS prefixes
    country_risk: pd.DataFrame | None = None      # user-supplied country risk ratings
    params: dict = field(default_factory=dict)

    def p(self, key, default):
        return self.params.get(key, default)


def severity(score: float) -> str:
    return "high" if score >= 0.75 else "medium" if score >= 0.45 else "low"


def make_flags(t_id: str, df: pd.DataFrame, *, entity_type: str, entity_id, entity_label, period, score,
               summary, evidence, caveats: str, hs6="", port="") -> pd.DataFrame:
    """Build flag rows from a frame of hits. Column arguments may be names or callables of the frame."""
    if df is None or df.empty:
        return pd.DataFrame(columns=FLAG_COLUMNS)

    def col(v):
        if callable(v):
            v = v(df)
        if isinstance(v, pd.Series):
            return v.set_axis(df.index) if len(v) == len(df) else v.reindex(df.index)
        if isinstance(v, (np.ndarray, list)):
            return pd.Series(v, index=df.index)
        if isinstance(v, str) and v in df.columns:
            return df[v]
        return pd.Series([v] * len(df), index=df.index)

    t = REGISTRY[t_id]
    s = col(score).astype(float).clip(0, 1)
    out = pd.DataFrame({
        "typology_id": t_id, "area": t.area, "entity_type": entity_type,
        "entity_id": col(entity_id).astype(str), "entity_label": col(entity_label).astype(str),
        "period": col(period).astype(str), "score": s.round(3), "severity": s.map(severity),
        "summary": col(summary).astype(str), "evidence": col(evidence).astype(str), "caveats": caveats,
        "hs6": col(hs6).astype(str), "port": col(port).astype(str),
    })
    return out.reset_index(drop=True)


def run_all(ctx: Context, only: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run every registered check. Returns (flags, run_log)."""
    from . import forced_labor, sanctions, tbml  # noqa: F401  (registers the typologies)

    frames, log = [], []
    for t in REGISTRY.values():
        if only and t.id not in only:
            continue
        try:
            df = t.run(ctx)
            status = "ran" if df is not None else "skipped: inputs missing"
        except Exception as e:  # one broken check must not sink the rest
            df, status = None, f"error: {type(e).__name__}: {e}"
        n = 0 if df is None else len(df)
        log.append({"typology_id": t.id, "area": t.area, "name": t.name, "status": status, "n_flags": n})
        if n:
            frames.append(df)
    flags = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FLAG_COLUMNS)
    flags = flags.sort_values(["score", "typology_id"], ascending=[False, True]).reset_index(drop=True)
    return flags, pd.DataFrame(log)


def entity_risk(flags: pd.DataFrame) -> pd.DataFrame:
    """Roll flags up per entity.

    Scores combine as independent signals, 1 - prod(1 - s), so several weak flags
    rank above one weak flag but never above certainty. Repeats of the same
    typology on the same entity (e.g. many months) count once at their max, so a
    chatty check cannot dominate the queue.
    """
    if flags.empty:
        return pd.DataFrame(columns=["entity_type", "entity_id", "entity_label", "risk", "severity",
                                     "n_flags", "n_typologies", "typologies", "areas"])
    best = flags.groupby(["entity_type", "entity_id", "typology_id"], as_index=False).agg(
        score=("score", "max"), n=("score", "size"), area=("area", "first"), label=("entity_label", "first"))

    def agg(g):
        return pd.Series({
            "entity_label": g["label"].iloc[0],
            "risk": 1 - float(np.prod(1 - g["score"].to_numpy())),
            "n_flags": int(g["n"].sum()), "n_typologies": len(g),
            "typologies": ", ".join(sorted(g["typology_id"])),
            "areas": ", ".join(sorted(set(g["area"]))),
        })

    out = best.groupby(["entity_type", "entity_id"]).apply(agg, include_groups=False).reset_index()
    out["risk"] = out["risk"].round(3)
    out["severity"] = out["risk"].map(severity)
    return out.sort_values(["risk", "n_typologies"], ascending=False).reset_index(drop=True)


def library() -> pd.DataFrame:
    from . import forced_labor, sanctions, tbml  # noqa: F401
    rows = []
    for t in REGISTRY.values():
        d = t.info()
        for k in ("sources", "data_needed", "innocent_explanations"):
            d[k] = " | ".join(d[k])
        rows.append(d)
    return pd.DataFrame(rows)
