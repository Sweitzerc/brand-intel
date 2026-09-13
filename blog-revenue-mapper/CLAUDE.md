# Blog Revenue Mapper — working context

Read this before touching anything in this directory.

The blog already earns the traffic. It has no path to a product. This project
maps each existing post to the right products and inserts a product block.
It does not write new blog content, and it does not touch the 308 product
descriptions again.

---

## Hard rules

### 1. No medical claims. Ever.

This is the Google Ads disapproval vector. It is a hard fail, not a style
note. A single one of these in body copy can cost the account.

Banned outright in any copy this project produces:

    treats, treatment for, prevents, prevention of, cures, heals,
    relieves, relief from, alleviates, reduces pain, eases pain,
    therapeutic, therapy, remedy, medically proven, clinically proven,
    doctor recommended, recommended by doctors, physician approved,
    FDA approved, medical device, prescription, diagnosis, diagnosed,
    rehabilitation, rehab aid, corrects, restores, improves your condition

Also banned: framing a condition as a diagnosis the reader has.
Write "if you want more stability on uneven ground", never "for your
arthritis". Describe the product's build and what it does. Never describe
what it does to a body or a condition.

`scripts/verify.py` fails any row whose `block_copy` contains a banned term.
Adding a term to the list is always allowed. Removing one is not.

### 2. Body copy says "Walking Stick", never "cane".

In customer-facing body copy the product is a **Walking Stick**.
"Cane" is fine in a URL, a handle, a product title that already ships with
it, a search query, or anywhere in this repo's own code and notes. It is not
fine in the prose of a `block_copy`.

### 3. Collins Place is stock. Missouri Returns is not.

`Missouri Returns` is a returns address, not a stocking location. Inventory
sitting there is not sellable. Count availability only at **Collins Place**.
`collect.py` already filters to the sellable location; do not undo that.

### 4. Copy to a file first, then push with a deterministic script.

No model tool call ever writes to Shopify. That is what produced the
Markdown/HTML hybrid mess across 308 product descriptions.

The only permitted path is:

    match.py  ->  out/mapping.draft.json
    verify.py ->  out/mapping.verified.json
    a human flips status to "approved"
    scripts/push.py reads approved rows and writes, after backing up

`push.py` defaults to `--dry-run` and requires an explicit flag to write.
Before any write it dumps the current article HTML to
`backups/<timestamp>/<handle>.html`.

Before writing `push.py`, verify the article update mutation name and its
required fields against the current Admin API version. It has changed across
versions; do not assume.

### 5. Brand voice: style over stigma.

Per the vault's **Brand Voice & Messaging Guide**. The product is an
accessory someone chooses, not equipment they were assigned. Confident,
plain, a little dry. Never pitying, never clinical, never a hard sell.
`block_copy` is 2-3 sentences plus the product name and price.

---

## Store facts

| | |
|---|---|
| Store | Canes Galore, www.canesgalore.com, Shopify Basic |
| Blog | one blog, handle `news`, at `/blogs/news/<article-handle>` |
| Articles | 155 total, 135 published |
| Sellable location | Collins Place |
| Site-wide conversion | 0.88% |
| Site-wide ATC rate | 2.37% |

---

## What the data actually is

Two things in the build spec do not match the reports sheet. Both matter.

**The 28-day window was a pull limit, not a data limit.** The Scorecard tab
says "Trailing 28 days, 3-day GSC lag" because that is the range the Monday
Apps Script asks for. Search Console retains roughly 16 months and the GA4
property has full history, so 90 days was always available.

Two backfills exist, and `score.py` picks them up automatically when their
files are present under `data/tabs/`:

    apps_script/         query x page, added to the existing weekly writer
      gsc_query_page.gs  project. PREFERRED: the scope and the property
                         string are already configured there. Writes a
                         GSC_Query_Page tab that collect.py picks up.
    scripts/lib_gsc.py   the same pull locally, needing GSC_ACCESS_TOKEN or a
                         service account key. Fallback only.
    ShopifyQL            sessions, cart additions, reached and completed
                         checkout by landing page over 90 days.

Columns from the reports sheet keep their `_28d` names. Shopify columns are
`_90d`. `ranking_window` on each row says which one drove the ranking.

Shopify sessions and GA4 sessions are different measurement systems and will
not agree. Do not compare a `_90d` figure against a `_28d` one and call the
difference a trend. Compare like with like.

**GSC_Queries has no `page` column** — but the API does. The sheet exports
queries and pages as two independent top-N lists with no join key.
`scripts/lib_gsc.py` pulls both dimensions together and writes
`data/tabs/gsc_query_page.csv`; when that file exists `score.py` uses the
real join and reports `intent_source = measured`. Without it,
`lib_attribute.py` estimates the mapping from slug and title tokens and
reports `intent_source = attributed`. Pages with too little support fall
back to the site prior and report `site_prior`.

Check the `intent_source` column before trusting any intent number.

**Intent is graded, not binary.** 267 of 275 queries on this site mention a
product noun, so a commercial/not flag has no variance at all. Each query
gets a purchase-proximity weight instead, and the tiers are ordered by how
close the searcher is to choosing a product:

| Weight | Rule | Example |
|---|---|---|
| 1.00 | condition + product noun + modifier | best walking cane for arthritis |
| 0.90 | explicit buying term | walking stick vs hiking pole |
| 0.80 | condition + product noun | cane for parkinsons |
| 0.70 | sizing question about a product | how to measure for a cane |
| 0.50 | bare product noun | walking cane |
| 0.30 | ambiguous | |
| 0.10 | informational | how to use a cane |
| 0.00 | navigational | canes galore |

`buying_intent_score` is the weighted mean of those, where each query's
weight is **impressions discounted by rank position** (`POSITION_WEIGHT` in
`score.py`, an approximate organic CTR curve). Weighting by impressions
alone lets a huge head term the page barely ranks for drown out the queries
that actually describe it.

**Some posts already have blocks.** Seven of the 38 trafficked posts already
contain a `cg-shop-block` product module. `has_product_link` in the spec is
too blunt, so the extractor reports four separate signals: the shop block
marker, direct `/products/` links, `/collections/` links, and `/search?q=`
links. A `/search?q=` link is **not** a product path; it drops the reader on
a results page.

The candidate filter treats only a real product module as an existing path
(`scoring.existing_path_signal: shop_block`). Posts whose only product path
is a collection link buried in prose converted at 0.08% last window, so a
link in prose is not a path.

**Most of the blog was edited after the window closed. This is a trap.**
The reporting window ends **2026-09-05**. 28 of the 38 scored posts, carrying
**87% of blog traffic**, were edited on 2026-09-09 to 09-11, including every
one of the seven that now has a block.

So in any row of `candidates.csv`, the traffic half and the HTML half
describe different versions of the page. Sessions and add-to-carts come from
before the edit; `has_shop_block` and the link counts come from the live page
today. **The add-to-cart figures on those posts say nothing about whether the
blocks work — there is no post-block data yet.**

`score.py` emits an `edited_after_window` column and prints a warning naming
the affected posts. Do not remove either. Do not read a low ATC on a flagged
row as evidence that its block failed.

---

## Layout

    config.yaml          sheet id, thresholds, window, store facts
    STATE.md             run-to-run memory: what shipped, what is open
    scripts/
      lib_sheet.py       read the reports sheet (Sheets API or saved export)
      lib_article.py     extract product-path signals from article HTML
      lib_intent.py      classify a query, with a purchase-proximity weight
      lib_attribute.py   estimate query -> page (see the gap above)
      collect.py         stage 1, read-only
      score.py           stage 2, read-only, writes out/candidates.csv
      match.py           stage 3, maker        (not built yet)
      verify.py          stage 4, checker      (not built yet)
      push.py            stage 5, dry-run default (not built yet)
    data/                cached inputs, gitignored
    out/                 candidates.csv, mapping.*.json, push_log.jsonl
    backups/             pre-write article HTML

## Running Phase 0

    pip install -r requirements.txt
    export SHOPIFY_STORE_DOMAIN=canes-galore.myshopify.com
    export SHOPIFY_ADMIN_TOKEN=...          # read-only
    python3 scripts/collect.py
    python3 scripts/score.py --show

`--source export` parses a saved spreadsheet export from
`data/raw/sheet_export.md` instead of calling the Sheets API, and
`--skip-products` runs without the Shopify token. Everything caches;
`--refresh` refetches.

## The weekly writer

The tabs are written by a standalone Apps Script project, "Canes Galore —
GA4 + Search Console Weekly Writer", triggered Monday 5-6 AM. It must land
before the Make watchdog at 7 AM and the Direction Check at 9 AM.

Confirmed from its source:

| | |
|---|---|
| GSC property | `https://www.canesgalore.com/` (URL-prefix) |
| GA4 property | `properties/358328482` |
| Window | `CONFIG.DAYS = 28`, a constant, not a data limit |
| Scopes | `webmasters.readonly` already granted |
| Landing page cap | `CONFIG.TOP_N = 100` |

**`GA_Landing` is truncated at 100 rows** across the whole site, because
`ga4Landing_` passes `TOP_N` as the GA4 limit. That is why the tab shows 38
blog pages while Shopify sees 103 over the same 28 days. Any count taken
from `GA_Landing` is a floor, not a total. Raising `TOP_N` widens both that
tab and `GSC_Queries`, which slices at `TOP_N * 3`.

**`gscRun_` sends one dimension per call** and does not paginate past
`rowLimit`. That is the whole reason query x page does not exist.
`apps_script/gsc_query_page.gs` adds it as a separate function with its own
trigger, filtered to `/blogs/news/` and paginated.

## There is no service account

The build spec refers to "the service account from the existing
canes-galore-scripts GCP project". No such key exists on Chris's machine and
none is known to have been provisioned. A **bound Apps Script runs as the
user who owns the sheet**, over their own OAuth grant, which is how the
Monday script reads and writes without any key.

That account also owns the Search Console property, confirmed via the
Supermetrics connection, which lists it as `galorebrandsusa@gmail.com` with
the property id `https://www.canesgalore.com/`. So the Search Console pull
belongs in Apps Script, next to the script that already runs.

Do not add a `GOOGLE_APPLICATION_CREDENTIALS` requirement to anything new
without checking that a key actually exists.

## Check the property type before blaming permissions

A URL-prefix property and a domain property are different strings:

    https://www.canesgalore.com/        URL-prefix
    sc-domain:canesgalore.com           domain

Passing the wrong one returns 403, which is indistinguishable from a
permissions failure. Both the Apps Script (`listSearchConsoleSites`) and
`lib_gsc.py --list-sites` print the exact strings. Read it, do not guess.
