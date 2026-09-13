# STATE — Blog Revenue Mapper

Run-to-run memory. Update this at the end of every session.

---

## Where the project is

**Phase 0 (collect + score) is built and has been run. Nothing has been
written to the store.** Stages 3-5 do not exist yet.

| Stage | Script | State |
|---|---|---|
| 1 Collect | `scripts/collect.py` | built, run |
| 2 Score | `scripts/score.py` | built, run |
| 3 Match (maker) | `scripts/match.py` | not built |
| 4 Verify (checker) | `scripts/verify.py` | not built |
| 5 Push | `scripts/push.py` | not built |

---

## Backfill status

The 28-day window was the Apps Script's date range, not a data limit.

| Source | Window | State |
|---|---|---|
| Shopify sessions by landing page | 90d + 28d | **done**, via ShopifyQL in `collect.py` |
| GSC query x page | 90d | **ready to run** — paste one file into the existing weekly writer, no setup |
| GA4 landing pages | 90d | not pulled; Shopify covers the same funnel |

The Shopify backfill widened the picture a lot: **100 blog landing pages
over 90 days against the 38 the 28-day GA export showed**, and 112 blog URLs
now scored.

**The service account does not exist.** The tabs are written by a standalone
Apps Script project, "Canes Galore — GA4 + Search Console Weekly Writer",
running as the sheet owner over OAuth. No key, nothing to provision. The
build spec's "canes-galore-scripts service account" was never real.

The script already holds `webmasters.readonly` and already has the correct
property string, so adding query x page needs no setup at all:

1. Paste `apps_script/gsc_query_page.gs` as a second file in that project.
2. Add a time-driven trigger for `exportQueryPage`, Monday 6-7 AM, between
   the writer at 5-6 and the Make watchdog at 7.
3. It writes a `GSC_Query_Page` tab, 90 days, filtered to `/blogs/news/`.
4. `collect.py` and `score.py` pick the tab up with no further changes and
   `intent_source` becomes `measured`.

Verified with a synthetic fixture: the join flips to measured and scored
pages get distinct intent values (0.759 and 0.605) instead of the flat 0.527
prior everything carries today.

`scripts/lib_gsc.py` remains as a local fallback; it needs `GSC_ACCESS_TOKEN`
or a real key. Supermetrics has Search Console authenticated but the team's
trial expired 2026-07-12, so that route is closed.

**Until the GSC backfill runs, the intent column barely works.** Only 18 of
68 candidates have enough attributed queries to score on their own data; the
rest fall back to the site prior of 0.527 and the ranking is effectively
traffic order. This is not a flaw in the intent model, it is starvation: 275
queries over 28 days with no page dimension cannot cover 112 pages.

---

## Last run

- Reporting period: **2026-08-09 → 2026-09-05** (trailing 28 days, 3-day GSC lag)
- Blog landing pages with traffic: **38**
- Blog URLs scored (GA + GSC union): **92**
- Articles in the store: **155** (135 published)

Baseline, written to `out/baseline.json`, is the number Phase 1 has to beat:

| | |
|---|---|
| Blog sessions | 4,552 |
| Blog add-to-carts | 6 |
| **Blog ATC rate** | **0.13%** |
| Blog purchases | 0 |
| Blog revenue | $0.00 |
| Site-wide ATC rate | 2.37% |

The blog converts to cart at roughly a **eighteenth** of the site-wide rate.
That is the gap this project exists to close.

---

## What Phase 0 found

1. **The premise holds.** 38 posts, 4,552 sessions, 6 add-to-carts, no
   revenue. The blog is 36.5% of landing sessions and converts to cart at
   roughly a eighteenth of the site-wide rate.

2. **Seven posts already have blocks — added after the data was collected.**
   Seven posts carry a `cg-shop-block` module, and 28 of the 38 scored posts
   (87% of blog traffic) were edited on 2026-09-09 to 09-11. The window
   closed 2026-09-05.

   This means **there is no post-block data at all yet**. The add-to-cart
   figures on those posts describe the pages before the blocks existed. The
   first read of this data — "blocks are in and they are not converting" —
   is wrong, and the `edited_after_window` column exists to stop anyone
   reaching it.

   The upside: `out/baseline.json` is a clean pre-treatment baseline for
   exactly those seven posts.

3. **Existing "product paths" are mostly not paths.** 19 posts' only product
   path is a collection link in prose. Those converted at 0.08%. One post
   links only to `/search?q=`, which drops the reader on a results page.
   The candidate filter therefore counts only a real product module.

4. **The sizing-guide cannibalisation is real and is worse than six URLs.**
   At least six posts compete for the measuring/height cluster:

   | Sessions | Handle |
   |---|---|
   | 656 | the-complete-guide-to-walking-cane-heights-find-your-perfect-fit |
   | 579 | how-to-measure-for-the-correct-walking-cane-height |
   | 190 | how-to-measure-your-walking-cane-find-the-perfect-fit |
   | 74 | how-to-measure-your-walking-cane |
   | 40 | the-canes-galore-sizing-guide-how-to-measure-for-a-walking-cane... |
   | — | measure-ideal-walking-stick |

   This is Phase 3 and it is now evidenced.

   Two of these already carry blocks, which makes consolidation more urgent,
   not less: the 301s should point at whichever URL keeps its block.

---

## Open issues

**1. GSC_Queries has no `page` column. (blocking a real intent score)**

The build spec assumes query → page. The sheet does not have it, so
`lib_attribute.py` estimates the mapping from slug and title tokens. Pages
with fewer than 3 attributed queries fall back to the site-wide intent prior
and are marked `intent_source = site_prior` in the CSV.

*Fix:* add the page dimension to the GSC query export in the Monday Apps
Script. One line. Until then the intent column ranks, it does not measure.

**2. The window is 28 days, not the 90 the spec assumed.** Columns are named
`_28d`. Do not relabel.

**3. No Shopify product catalog pulled yet.** `collect.py --skip-products`
was used. Stage 3 needs `data/products.json`; run `collect.py` with
`SHOPIFY_ADMIN_TOKEN` set before starting the matcher.

---

## Phase 1 — the ten by hand

Not started. Record the ten here as they ship, with the date and the block
inserted, so Phase 2 can be measured against them.

| # | Handle | Products inserted | Date | Notes |
|---|---|---|---|---|
| | | | | |

---

## Next action

**Do not start Phase 1 by inserting more blocks. Wait for data first.**

Seven posts covering 2,571 sessions were blocked days ago and have never been
measured. A full 28-day window closes around **2026-10-07**. Re-run
`collect.py --refresh` and `score.py` then and compare `blog_atc_rate_pct`
against the 0.132% in `out/baseline.json`.

That single comparison answers the question the whole project rests on, at a
cost of one re-run. Building stages 3-5 first would mean inserting 30 more
blocks of a design that has never been shown to work.

In the meantime, two things are worth doing and neither writes to the store:

1. **Add the page dimension to the GSC query export** in the Monday Apps
   Script. It is one line and it turns `buying_intent_score` from an estimate
   into a measurement.

2. **Phase 3, the sizing-guide consolidation.** It does not depend on block
   performance and the data to choose the canonical URL is in
   `out/candidates.csv` now.

If the October re-run shows the blocks moved ATC, build stages 3-5 and work
down `candidates.csv`. If it did not, the block design is the problem and
more of them will not help.
