"""
Brand Intel: an open-source brand monitoring dashboard.

Each tab is one module of the framework (see brand_intel/), and most are
adapted from a Bellingcat open-source research tool:

  Pulse            collect mentions -> score tone -> bucket topics -> trend
  Conversation     hashtag/term co-occurrence      (tiktok-hashtag-analysis)
  Lookalikes       name variants + typo domains    (name-variant-search)
  Site Network     shared tracking IDs over time   (wayback-google-analytics)
  SEC Filings      full-text search of EDGAR       (EDGAR)
  Evidence Locker  archive + hash a page           (auto-archiver)
  Toolkit          more Bellingcat tools worth knowing

Run locally with `python app.py`, or deploy free as a Hugging Face Space.
"""

import csv
import tempfile

import gradio as gr

from brand_intel import charts, storage
from brand_intel.collectors import COLLECTORS
from brand_intel.core import load_config
from brand_intel.investigators import archive, lookalikes, sec, site_network
from brand_intel.analyzers import ai_enabled, sentiment
from brand_intel.pipeline import run_scan

CFG = load_config()
BRAND = CFG.get("brand", "")
ITEM_HEADERS = ["Tone", "Title", "Source", "Platform", "Engagement", "Topics", "Link"]


# ---- Pulse ----------------------------------------------------------------

def do_scan(query, sources, limit):
    query = (query or "").strip()
    if not query:
        return "Type a brand, product or topic, then press **Scan**.", None, None, None, None, None, None
    scan = run_scan(query, sources or list(COLLECTORS), int(limit))
    if not scan.items:
        msg = f"No public mentions found for **{query}**."
        if scan.errors:
            msg += "\n\nSources that failed:\n" + "\n".join(f"- {e}" for e in scan.errors)
        return msg, None, None, None, None, None, scan

    ranked = sorted(scan.items, key=lambda i: i.score or 0, reverse=True)
    pos = sum(1 for i in ranked if (i.score or 0) > 0.2)
    neg = sum(1 for i in ranked if (i.score or 0) < -0.2)
    loudest = max(scan.items, key=lambda i: i.engagement)
    summary = (
        f"### {query}\n"
        f"**Overall tone {scan.avg:+.2f} ({sentiment.mood(scan.avg)})** · "
        f"{len(ranked)} mentions · {pos} positive / {neg} negative\n\n"
        f"- **Most positive:** [{ranked[0].title}]({ranked[0].url})\n"
        f"- **Most negative:** [{ranked[-1].title}]({ranked[-1].url})\n"
    )
    if loudest.engagement:
        summary += f"- **Most engagement ({loudest.engagement}):** [{loudest.title}]({loudest.url})\n"
    if scan.errors:
        summary += "\n<sub>Skipped: " + "; ".join(scan.errors) + "</sub>"

    rows = [[i.score, i.title, i.source, i.platform, i.engagement, ", ".join(i.topics), i.url]
            for i in ranked]
    topic_rows = scan.topics()
    return (
        summary,
        charts.tone_by_group(scan.by_source(), "Tone by platform"),
        charts.tone_by_group(topic_rows, "Tone by topic") if topic_rows else None,
        charts.trend(storage.history(query), query),
        rows,
        export_csv(rows),
        scan,
    )


def export_csv(rows):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", prefix="brand-intel-")
    writer = csv.writer(f)
    writer.writerow(ITEM_HEADERS)
    writer.writerows(rows)
    f.close()
    return f.name


# ---- Conversation ---------------------------------------------------------

def do_conversation(scan):
    if not scan or not scan.items:
        return "Run a scan on the **Pulse** tab first.", None, None
    terms, pairs = scan.conversation()
    note = (f"Terms and hashtags that appear alongside **{scan.query}** across "
            f"{len(scan.items)} mentions. Pairs show what gets talked about together.")
    return note, charts.counts(terms[:20], "Top terms & hashtags"), [[p, n] for p, n in pairs]


# ---- Lookalikes -----------------------------------------------------------

def do_lookalikes(brand, domain, check_dns):
    variants = "\n".join(f"- `{v}`" for v in lookalikes.search_variants(brand or BRAND))
    domain = (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    if not domain:
        return variants, []
    if check_dns:
        rows = lookalikes.check_domains(domain, CFG.get("tlds", ["com"]))
        rows = [[d, how, "registered" if ip else "", ip] for d, how, ip in rows]
    else:
        rows = [[d, how, "not checked", ""] for d, how in
                lookalikes.candidate_domains(domain, CFG.get("tlds", ["com"]))]
    return variants, rows


# ---- Site network ---------------------------------------------------------

def do_network(domains_text, history):
    domains = [d.strip().lower() for d in (domains_text or "").replace(",", "\n").splitlines() if d.strip()]
    if not domains:
        return "Enter one domain per line.", [], []
    rows, errors = [], []
    for d in domains:
        r, e = site_network.scan_domain(d, history)
        rows += r
        errors += e
    shared = site_network.shared_ids(rows)
    msg = (f"**{len(shared)} tracking ID(s) shared between domains.** "
           "A shared ID strongly suggests the same owner or agency." if shared
           else "No IDs shared between these domains.")
    if errors:
        msg += "\n\n<sub>Could not read: " + "; ".join(errors[:8]) + "</sub>"
    return msg, [list(r) for r in shared], [list(r) for r in site_network.merge_sightings(rows)]


# ---- SEC ------------------------------------------------------------------

def do_sec(phrase, forms, start, end):
    if not (phrase or "").strip():
        return "Enter a phrase to search for.", []
    try:
        rows = sec.search(phrase.strip(), forms.strip(), start.strip(), end.strip())
    except Exception as exc:
        hint = "" if CFG.get("contact_email") else " Set `contact_email` in config.yaml - EDGAR requires it."
        return f"EDGAR search failed: {exc}.{hint}", []
    return f"{len(rows)} filing(s) mention **\"{phrase}\"** (EDGAR returns up to 100 per search).", [list(r) for r in rows]


# ---- Evidence -------------------------------------------------------------

def do_archive(urls_text):
    for url in [u.strip() for u in (urls_text or "").splitlines() if u.strip()]:
        storage.save_evidence(archive.archive(url))
    return [list(r) for r in storage.evidence()]


TOOLKIT = """
More Bellingcat tools that fit brand work, all free and open source:

| Tool | Use it to |
|---|---|
| [auto-archiver](https://github.com/bellingcat/auto-archiver) | Archive social posts and videos in bulk from a Google Sheet - the full version of the Evidence Locker |
| [wayback-google-analytics](https://github.com/bellingcat/wayback-google-analytics) | The original tracking-ID tool behind Site Network, with Excel export |
| [EDGAR](https://github.com/bellingcat/EDGAR) | Command-line SEC search, including RSS feeds of new filings |
| [tiktok-hashtag-analysis](https://github.com/bellingcat/tiktok-hashtag-analysis) | Scrape and chart TikTok hashtags directly |
| [smart-image-sorter](https://github.com/bellingcat/smart-image-sorter) | Sort a folder of product or ad images by label (e.g. "counterfeit", "lifestyle") |
| [instagram-location-search](https://github.com/bellingcat/instagram-location-search) | Find Instagram location tags near a store or event |
| [octosuite](https://github.com/bellingcat/octosuite) | Investigate GitHub accounts and organizations |
| [Online Investigations Toolkit](https://bellingcat.gitbook.io/toolkit) | Bellingcat's catalog of hundreds of research tools |

Only collect public information and respect each site's terms of service.
"""


# ---- Layout ---------------------------------------------------------------

with gr.Blocks(title="Brand Intel") as demo:
    scan_state = gr.State()
    gr.Markdown(
        "# Brand Intel\n"
        "Open-source brand monitoring built on [Bellingcat](https://github.com/bellingcat)'s research methods. "
        f"<sub>Analysis mode: {'AI models' if ai_enabled() else 'fast keyword (lite)'}</sub>"
    )

    with gr.Tab("Pulse"):
        with gr.Row():
            q = gr.Textbox(label="Brand, product or topic", value=BRAND, scale=4)
            btn = gr.Button("Scan", variant="primary", scale=1)
        with gr.Row():
            srcs = gr.CheckboxGroup(list(COLLECTORS), value=list(COLLECTORS), label="Sources")
            lim = gr.Slider(5, 100, value=25, step=5, label="Max items per source")
        gr.Markdown("Quick picks: " + " · ".join(f"`{c}`" for c in [BRAND, *CFG.get("competitors", [])] if c))
        summary = gr.Markdown()
        with gr.Row():
            src_plot = gr.Plot(label="By platform")
            topic_plot = gr.Plot(label="By topic")
        trend_plot = gr.Plot(label="Trend")
        table = gr.Dataframe(headers=ITEM_HEADERS, label="All mentions", wrap=True)
        download = gr.File(label="Download CSV")
        outs = [summary, src_plot, topic_plot, trend_plot, table, download, scan_state]
        btn.click(do_scan, [q, srcs, lim], outs)
        q.submit(do_scan, [q, srcs, lim], outs)

    with gr.Tab("Conversation"):
        conv_btn = gr.Button("Map conversation from last scan", variant="primary")
        conv_note = gr.Markdown()
        conv_plot = gr.Plot()
        conv_pairs = gr.Dataframe(headers=["Appear together", "Mentions"], label="Co-occurring terms")
        conv_btn.click(do_conversation, scan_state, [conv_note, conv_plot, conv_pairs])

    with gr.Tab("Lookalikes"):
        gr.Markdown("Spellings to monitor, and domains a copycat or scammer might register.")
        with gr.Row():
            lb = gr.Textbox(label="Brand name", value=BRAND)
            ld = gr.Textbox(label="Your domain", value=(CFG.get("domains") or [""])[0])
            dns = gr.Checkbox(label="Check which are registered (DNS)", value=True)
        look_btn = gr.Button("Generate", variant="primary")
        variants_md = gr.Markdown()
        look_table = gr.Dataframe(headers=["Domain", "Technique", "Status", "IP"], label="Lookalike domains")
        look_btn.click(do_lookalikes, [lb, ld, dns], [variants_md, look_table])

    with gr.Tab("Site Network"):
        gr.Markdown("Find sites that share Google Analytics, Tag Manager, Ads, AdSense or Meta Pixel IDs, "
                    "now or in past Wayback Machine snapshots.")
        nd = gr.Textbox(label="Domains (one per line)", lines=4,
                        value="\n".join(CFG.get("domains", [])))
        nh = gr.Checkbox(label="Include Wayback Machine history (slower)", value=True)
        net_btn = gr.Button("Scan sites", variant="primary")
        net_md = gr.Markdown()
        net_shared = gr.Dataframe(headers=["Tracking ID", "Type", "Shared by"], label="Shared IDs")
        net_all = gr.Dataframe(headers=["Domain", "Tracking ID", "Type", "Seen"], label="All IDs found")
        net_btn.click(do_network, [nd, nh], [net_md, net_shared, net_all])

    with gr.Tab("SEC Filings"):
        gr.Markdown("Search every SEC filing since 2001 for an exact phrase.")
        with gr.Row():
            sp = gr.Textbox(label="Phrase", value="walking canes", scale=3)
            sf = gr.Textbox(label="Form types (optional)", placeholder="10-K,10-Q,8-K")
            ss = gr.Textbox(label="From", placeholder="YYYY-MM-DD")
            se = gr.Textbox(label="To", placeholder="YYYY-MM-DD")
        sec_btn = gr.Button("Search EDGAR", variant="primary")
        sec_md = gr.Markdown()
        sec_table = gr.Dataframe(headers=["Filed", "Company", "Form", "Link"], wrap=True)
        sec_btn.click(do_sec, [sp, sf, ss, se], [sec_md, sec_table])

    with gr.Tab("Evidence Locker"):
        gr.Markdown("Save a public Wayback Machine copy of a page and log a SHA-256 fingerprint "
                    "of what you saw - for counterfeit listings, fake reviews, copied photos.")
        eu = gr.Textbox(label="URLs to preserve (one per line)", lines=3)
        ev_btn = gr.Button("Archive", variant="primary")
        ev_table = gr.Dataframe(
            headers=["Captured (UTC)", "URL", "Title", "SHA-256", "Wayback copy", "Note"],
            value=[list(r) for r in storage.evidence()], wrap=True)
        ev_btn.click(do_archive, eu, ev_table)

    with gr.Tab("Toolkit"):
        gr.Markdown(TOOLKIT)


if __name__ == "__main__":
    demo.launch()
