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

1. **The premise holds.** 38 posts, ~4,550 sessions, 6 add-to-carts, no
   revenue. Most have no path to a product at all.

2. **Two posts already have blocks.** The top two sizing guides contain a
   `cg-shop-block` module. They are excluded from `candidates.csv` by
   default; re-run with `--all` to see them. Their combined 1,235 sessions
   produced 2 add-to-carts, so the block alone is not sufficient — worth
   understanding before inserting 30 more.

3. **The sizing-guide cannibalisation is real and is worse than six URLs.**
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

4. **Several posts link only to `/search?q=`.** A site-search link is not a
   path to a product. The extractor counts these separately and they do not
   satisfy `has_product_link`.

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

Read `out/candidates.csv`. Decide whether the top 10 are the right 10 before
any code writes to the store.
