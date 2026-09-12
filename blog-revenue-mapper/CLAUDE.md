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

**The window is 28 days, not 90.** The Scorecard tab says
"Trailing 28 days, 3-day GSC lag". Every column is named `_28d` for that
reason. Do not relabel them `_90d`.

**GSC_Queries has no `page` column.** The spec's `buying_intent_score` is
defined as the share of *a page's* queries that look commercial, but the
sheet exports queries and pages as two independent top-N lists with no join
key. `lib_attribute.py` estimates the mapping by matching query terms
against each page's slug and title, and every row carries
`intent_query_support` so a page scored off one weak match is visibly
different from one scored off twelve.

The real fix is a one-line change to the Monday Apps Script: export the GSC
query report with both the query and page dimensions. Until then, read
`buying_intent_score` as a ranking aid, not a measurement.

**Some posts already have blocks.** The two highest-traffic sizing guides
already contain a `cg-shop-block` product module. `has_product_link` in the
spec is too blunt, so the extractor reports four separate signals: the shop
block marker, direct `/products/` links, `/collections/` links, and
`/search?q=` links. A `/search?q=` link is **not** a product path; it drops
the reader on a results page. Only the first three count.

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
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/canes-galore-scripts.json
    export SHOPIFY_STORE_DOMAIN=canes-galore.myshopify.com
    export SHOPIFY_ADMIN_TOKEN=...          # read-only
    python3 scripts/collect.py
    python3 scripts/score.py --show

Without credentials, `--source export` parses a saved spreadsheet export from
`data/raw/sheet_export.md` instead, and `--skip-products` runs without the
Shopify token. Everything caches; `--refresh` refetches.
