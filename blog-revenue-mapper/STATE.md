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

## Next action (updated 2026-09-13)

**1. Add the weekly trigger.** `exportQueryPage` ran once by hand and wrote
16,767 rows. Without a time-driven trigger (Monday 6-7 AM) the tab goes
stale and the intent scores drift out of date. Two minutes.

**2. Do Phase 3 before Phase 1. They collide.**

Seven URLs compete for the measuring/sizing cluster, splitting 26,215
impressions and 269 clicks:

| sz clicks | sz impr | avg pos | sess 90d | handle |
|---|---|---|---|---|
| 153 | 13,146 | 6.9 | 2,513 | the-complete-guide-to-walking-cane-heights-find-your-perfect-fit |
| 67 | 4,339 | 14.5 | 947 | how-to-measure-your-walking-cane-find-the-perfect-fit |
| 29 | 4,853 | 13.7 | 1,770 | how-to-measure-for-the-correct-walking-cane-height |
| 14 | 2,382 | 23.8 | 350 | how-to-measure-your-walking-cane |
| 5 | 905 | 23.2 | 105 | the-canes-galore-sizing-guide-how-to-measure... |
| 1 | 238 | 54.2 | 37 | how-to-size-a-walking-cane-guide |
| 0 | 352 | 77.4 | 11 | measure-ideal-walking-stick |

Canonical is unambiguous: **the-complete-guide-to-walking-cane-heights**
wins on clicks, impressions and position together, and it already carries a
`cg-shop-block`. 301 the other six into it and merge their content.

**The collision:** ranks 1 and 2 of `candidates.csv` are both in this
cluster. Inserting a product block into
`how-to-measure-your-walking-cane-find-the-perfect-fit` and then 301-ing it
away is wasted work. Consolidate first, then re-run `score.py` and take the
new top of the list.

**3. Wait for the block verdict.** Unchanged: the seven posts blocked
2026-09-09 to 09-11 are the running experiment and have no post-block data.
A full window closes about **2026-10-07**.

## Smaller fixes, any time

- **`CONFIG.TOP_N = 100` in the weekly writer truncates `GA_Landing`** to
  100 landing pages site-wide, which is why it showed 38 blog pages while
  Shopify saw 103. Raising it widens `GSC_Queries` too, which slices at
  `TOP_N * 3`.
- **Six blog URLs draw impressions but match no article**, and read as
  corrupted variants of real handles (`the-complete-day-...` for
  `the-complete-guide-to-...`; `like-id-was-made-for-you` for `like-it-`).
  Probably 404s worth redirecting.
- **Make scenario 6191825 reads fixed ranges** (`GSC_Queries` A1:I120,
  `GSC_Pages` A1:J60), so the Monday email reasons over the top 119 queries
  and 59 pages of 300 and 1,000.

## Superseded next action

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
