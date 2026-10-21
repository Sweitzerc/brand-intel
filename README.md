---
title: Brand Intel
emoji: 🛰️
colorFrom: blue
colorTo: red
sdk: gradio
app_file: app.py
pinned: false
---

# Brand Intel

An open-source brand monitoring dashboard. Its methods come from
[Bellingcat](https://github.com/bellingcat)'s open-source investigation tools.
It uses free public data only and needs no API keys.

## What's in the dashboard

| Tab | What it does | Based on |
|---|---|---|
| **Pulse** | Collects mentions from Google News, Reddit and Hacker News. It scores each one's tone, sorts it into topics, and charts tone over time. Results download as CSV. | the original app + [smart-image-sorter](https://github.com/bellingcat/smart-image-sorter) (zero-shot labels) |
| **Conversation** | Shows the hashtags and terms that show up alongside your brand, and which ones appear together | [tiktok-hashtag-analysis](https://github.com/bellingcat/tiktok-hashtag-analysis) |
| **Lookalikes** | Lists spellings of the brand name to monitor. It also lists typo and lookalike domains and checks which ones are registered. | [name-variant-search](https://github.com/bellingcat/name-variant-search) |
| **Site Network** | Finds websites that share Google Analytics, Tag Manager, Ads, AdSense or Meta Pixel IDs, checking both today's site and past Wayback Machine snapshots | [wayback-google-analytics](https://github.com/bellingcat/wayback-google-analytics) |
| **SEC Filings** | Searches the full text of every SEC filing since 2001 | [EDGAR](https://github.com/bellingcat/EDGAR) |
| **Evidence Locker** | Saves a Wayback Machine copy of a page and logs a SHA-256 fingerprint of its content | [auto-archiver](https://github.com/bellingcat/auto-archiver) |
| **Toolkit** | Links to more Bellingcat tools that are useful for brand work | |

## Run it

```bash
pip install -r requirements.txt
python app.py            # open http://127.0.0.1:7860
```

**Lite mode.** Run `BRAND_INTEL_LITE=1 python app.py` to skip the AI models and use fast keyword scoring
instead. Lite mode starts instantly and runs on small machines.

**Free hosting.** Create a Gradio Space on Hugging Face and push this repo to it. The header at the top of this
file configures the Space. Free Spaces wipe their disk on restart. To keep scan history and the
evidence log, turn on persistent storage and set the `BRAND_INTEL_DB=/data/brand_intel.db` variable.

## Configure

Edit `config.yaml` to set:

- your brand name
- competitors
- your domains
- topic buckets
- a contact email (the SEC EDGAR search requires one)

## Framework layout

```
brand_intel/
  core.py            Item model, config, shared HTTP session
  pipeline.py        COLLECT -> ANALYZE -> STORE (usable without the UI)
  collectors/        news.py, reddit.py, hackernews.py   -> list[Item]
  analyzers/         sentiment.py, topics.py, conversation.py
  investigators/     lookalikes.py, site_network.py, sec.py, archive.py
  storage.py         SQLite: scan history + evidence log
  charts.py          Plotly figures
app.py               Gradio dashboard (one tab per module)
tests/               offline tests (pytest)
```

**Adding a data source.** Write `collectors/<name>.py` with a `collect(query, limit) -> list[Item]` function,
then register it in `collectors/__init__.py`. It then appears as a checkbox on the Pulse tab.

**Scripting a scan.** You can run a scan without the dashboard, for example from a scheduled job:

```python
from brand_intel.pipeline import run_scan
scan = run_scan("Canes Galore", ["Google News", "Reddit"])
print(scan.avg, scan.topics())
```

## Ground rules

- Collect only public information.
- Respect each site's terms of service and rate limits.
- Treat a shared tracking ID or a lookalike domain as a lead to verify, not as proof.
