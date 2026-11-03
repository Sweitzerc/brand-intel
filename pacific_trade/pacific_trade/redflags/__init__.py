"""Red-flag typology checks. See framework.py for the flag schema and roll-up."""

from __future__ import annotations

import pandas as pd

from ..config import CROSSWALK_DIR
from .framework import AREAS, FLAG_COLUMNS, REGISTRY, Context, entity_risk, library, run_all

__all__ = ["AREAS", "FLAG_COLUMNS", "REGISTRY", "Context", "entity_risk", "library", "run_all", "load_reference"]


def load_reference() -> dict:
    """Reference tables kept in crosswalks/ (DOL goods mapping, your country risk ratings)."""
    out = {"dol_goods": pd.read_csv(CROSSWALK_DIR / "dol_goods_hs.csv", dtype=str)}
    risk = pd.read_csv(CROSSWALK_DIR / "country_risk.csv", dtype=str)
    out["country_risk"] = risk if len(risk) else None
    return out
