"""Offline tests: every module is exercised against canned responses."""

import os

os.environ["BRAND_INTEL_LITE"] = "1"

import pytest

from brand_intel import pipeline, storage
from brand_intel.analyzers import conversation, sentiment, topics
from brand_intel.collectors import hackernews, news, reddit
from brand_intel.core import Item
from brand_intel.investigators import lookalikes, sec, site_network

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Canes Galore launches elegant new cane line</title><link>https://ex.com/a</link>
<source url="https://ex.com">Example News</source><pubDate>Mon, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Customer says Canes Galore cane arrived broken, shipping late</title><link>https://ex.com/b</link>
<source url="https://ex.com">Other News</source></item>
</channel></rss>"""

REDDIT = {"data": {"children": [{"data": {
    "title": "Love my Canes Galore cane #mobility", "permalink": "/r/canes/1",
    "subreddit": "canes", "created_utc": 1760000000, "selftext": "So stylish and sturdy",
    "score": 12, "num_comments": 3}}]}}

HN = {"hits": [{"title": None, "story_title": "Ask HN: walking canes", "comment_text": "Canes Galore was helpful",
                "objectID": "42", "created_at": "2026-10-01", "points": None, "num_comments": None}]}

EDGAR = {"hits": {"hits": [{"_id": "0001234567-26-000001:form10k.htm", "_source": {
    "ciks": ["0001234567"], "display_names": ["Mobility Co (CIK 0001234567)"],
    "file_date": "2026-03-01", "form": "10-K"}}]}}


def test_collector_parsers():
    n = news.parse(RSS, 10)
    assert [i.source for i in n] == ["Example News", "Other News"]
    r = reddit.parse(REDDIT, 10)[0]
    assert r.source == "r/canes" and r.engagement == 15 and r.url.endswith("/r/canes/1")
    h = hackernews.parse(HN, 10)[0]
    assert h.title == "Ask HN: walking canes" and h.url.endswith("id=42") and h.engagement == 0


def test_sentiment_and_topics():
    items = news.parse(RSS, 10) + reddit.parse(REDDIT, 10)
    sentiment.analyze(items)
    assert items[0].score > 0 and items[1].score < 0 and items[2].score > 0
    topics.analyze(items, {"Shipping": ["shipping"], "Style": ["elegant", "stylish"]})
    assert items[1].topics == ["Shipping"] and "Style" in items[0].topics
    assert topics.breakdown(items)[0][0] == "Style"


def test_conversation_counts_hashtags_and_excludes_query():
    items = [Item("Canes #mobility style", "", "", "x"), Item("Galore #mobility style", "", "", "x")]
    terms, pairs = conversation.analyze(items, "Canes Galore")
    assert ("#mobility", 2) in terms and all(t not in ("canes", "galore") for t, _ in terms)
    assert ("#mobility + style", 2) in pairs


def test_lookalikes():
    v = lookalikes.search_variants("Canes Galore")
    assert {"canesgalore", "#canesgalore", "canes-galore", "Cane Galore"} <= set(v)
    pairs = dict(lookalikes.candidate_domains("canesgalore.com", ["com", "net"]))
    assert pairs["canesgalore.net"] == "other TLD"
    assert pairs["canesgal0re.com"] == "lookalike character"
    assert pairs["canesgalre.com"] == "missing letter"
    assert "canesgalore.com" not in pairs


def test_site_network_ids_and_sharing():
    html = """<script>gtag('config','G-ABC123XYZ9');</script> GTM-K9X2PQ UA-12345-1
              fbq('init', '123456789012345'); ca-pub-1234567890123456"""
    ids = site_network.extract_ids(html)
    assert ids == {"G-ABC123XYZ9": "GA4", "GTM-K9X2PQ": "Tag Manager", "UA-12345-1": "Universal Analytics",
                   "123456789012345": "Meta Pixel", "ca-pub-1234567890123456": "AdSense"}
    rows = [("a.com", "GTM-1", "Tag Manager", "live"), ("b.com", "GTM-1", "Tag Manager", "2021"),
            ("b.com", "GTM-1", "Tag Manager", "2023"), ("a.com", "G-2", "GA4", "live")]
    assert site_network.shared_ids(rows) == [("GTM-1", "Tag Manager", "a.com, b.com")]
    assert ("b.com", "GTM-1", "Tag Manager", "2021, 2023") in site_network.merge_sightings(rows)


def test_sec_parse():
    (filed, company, form, link), = sec.parse(EDGAR)
    assert form == "10-K" and link == \
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/form10k.htm"


def test_pipeline_dedupes_and_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAND_INTEL_DB", str(tmp_path / "t.db"))
    dup = news.parse(RSS, 10)
    monkeypatch.setattr(pipeline, "collect_all", lambda q, s, l: (dup + news.parse(RSS, 10), ["Reddit: 403"]))
    scan = pipeline.run_scan("Canes Galore", ["Google News"])
    assert len(scan.items) == 2 and scan.errors == ["Reddit: 403"]
    assert storage.history("canes galore")[0][2] == 2


def test_app_handlers(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAND_INTEL_DB", str(tmp_path / "t.db"))
    import app
    monkeypatch.setattr(pipeline, "collect_all",
                        lambda q, s, l: (news.parse(RSS, 10) + reddit.parse(REDDIT, 10), []))
    summary, *_, rows, csv_path, scan = app.do_scan("Canes Galore", ["Google News"], 25)
    assert "Overall tone" in summary and len(rows) == 3 and csv_path.endswith(".csv")
    note, fig, pairs = app.do_conversation(scan)
    assert fig is not None
    variants, domains = app.do_lookalikes("Canes Galore", "https://canesgalore.com/", False)
    assert "canesgalore" in variants and domains
