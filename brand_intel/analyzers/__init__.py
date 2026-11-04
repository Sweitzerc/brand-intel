"""
Analyzers enrich collected Items in place.

AI models are optional. Set BRAND_INTEL_LITE=1 (or skip installing
`transformers`) and every analyzer falls back to a fast keyword method,
so the dashboard still runs on tiny machines.
"""

import os


def ai_enabled() -> bool:
    if os.environ.get("BRAND_INTEL_LITE") == "1":
        return False
    try:
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True
