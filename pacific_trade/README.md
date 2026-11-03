# pacific_trade

A pipeline that combines three public sources to describe how semiconductors (HS 8541–8544) and critical minerals (HS 2609–2621, 2846) reach US Pacific ports:

| Source | Used for | Grain |
|---|---|---|
| NOAA Marine Cadastre AIS | Port calls, dwell time, anchorage waits | vessel × position (US coastal receivers only) |
| Census International Trade API (`porths`, `hs`) | The core trade table, with values and weights by vessel, container and air | port of unlading × HS6 × origin country × month |
| UN Comtrade | Global exporter concentration and a cross-check against Census | reporter × partner × HS6 × month/year |

It implements the linkage proposed in the data-source recon (2026-10-19). **No source in scope says which ship carried which goods.** Sources are joined at **port × month** and **country × HS6** only. Anything at vessel level comes from an allocation model, and every such table is labelled `est_` with `estimated=True`.

## Setup

```bash
cd pacific_trade
pip install -e '.[dev]'
cp .env.example .env   # then fill in the keys and export them
export CENSUS_API_KEY=...     # required  https://api.census.gov/data/key_signup.html
export COMTRADE_API_KEY=...   # recommended https://comtradeplus.un.org/MyComtrade/APIPortal
export PACIFIC_TRADE_DATA=./data
python -m pytest              # offline tests, no network needed
```

The machine needs outbound HTTPS to `noaaocm.blob.core.windows.net`, `coast.noaa.gov` (≤2024 AIS), `api.census.gov`, `comtradeapi.un.org` and `msi.nga.mil`.

## Run order

```bash
python -m pacific_trade wpi-fetch                                   # World Port Index -> real port coordinates
python -m pacific_trade ais-fetch --start 2026-04-01 --end 2026-06-30 --workers 4
python -m pacific_trade census-pull --start 2026-04 --end 2026-06   # crosswalk ports, HS 8541-8544 + minerals
python -m pacific_trade census-pull --start 2026-04 --end 2026-06 --national   # optional, for the Comtrade cross-check
python -m pacific_trade comtrade-pull --year 2025 --months 202604 202605 202606
python -m pacific_trade portcalls --start 2026-04-01 --end 2026-06-30
python -m pacific_trade lists-fetch                                 # sanctions / export-control lists for red flags
python -m pacific_trade build --csv                                 # --method draft_drop for the sensitivity run
```

To preview the dashboard without any keys or downloads, run `python -m pacific_trade demo`. It writes `data/out/dashboard.html` built from **synthetic** data, and both the page and the report carry a demo banner.

Every step can be re-run safely. AIS days that are already filtered get skipped, and Census and Comtrade responses are cached under `data/cache/`. The cache key leaves out the API key, so re-runs don't spend API quota.

**AIS cost:** each day is a 200–300 MB `.zst` file. It is streamed and filtered without being written to disk (about 25 s/day according to the recon), and only cargo and tanker rows (types 70–89) inside the Hawaii, West Coast and Guam/CNMI boxes are kept. A quarter is about 20 GB of download. For ≤2024 data the legacy zip has to be downloaded to a temp file before it can be read. Both schemas are normalised to the 2025 snake_case schema.

## Outputs (`data/out/`)

| Table | What it is |
|---|---|
| `fact_port_month_hs6_origin` | Fused fact table: Census values + AIS port-month stats (`ais_*`) + Comtrade origin world-export share and global exporter HHI |
| `origin_hhi` | Supplier-country HHI per port × HS6 (vessel value) |
| `port_hhi` | How concentrated US vessel imports of each HS6 are across ports |
| `mode_split` | Vessel / air / containerised share and USD/kg per port × HS6 (**check this first**, see below) |
| `global_exporter_hhi` | World exporter HHI per HS6 × year (Comtrade, partner = World) |
| `census_vs_comtrade` | Census general imports vs Comtrade US-reported imports (needs `--national`) |
| `port_calls`, `port_month_ais`, `vessel_registry` | AIS-derived calls, port × month stats, and MMSI → IMO identity resolution |
| `est_vessel_edges` | **Modelled** vessel × port × month × HS6 tonnage and value |
| `est_vessel_spof`, `est_vessel_exposure` | **Modelled** share on the largest single vessel, and value carried per vessel |
| `est_unallocated` | Census tonnage in port-months where AIS saw no eligible call |
| `report.md` | Summary of the above |
| `dashboard.html` | Interactive dashboard (see below) |

## Dashboard

`build` writes `data/out/dashboard.html`, a single self-contained file with no external scripts or fonts. It opens offline, can be emailed, or can be hosted as a static page. Filters for month range, commodity group, HS6 line and port re-aggregate the embedded tables in the browser. Every chart has a table view, and the page supports light and dark themes and phone-width screens.

| Section | Shows |
|---|---|
| Headline tiles | Vessel import value, all-mode value, share arriving by vessel, vessel weight, supplier HHI, AIS cargo calls |
| Trade (Census) | Transport-mode split, vessel value by port, countries of origin, monthly trend |
| Port activity (AIS) | Cargo calls per month; mean hours moored and waiting at anchor |
| Global supply (Comtrade) | World exporter HHI per HS6 against the 2,500 line |
| Vessel exposure (modelled) | Estimated value per ship and single-ship dependence, under a "MODELLED, not observed" banner |

The dashboard embeds the tables it uses, so anyone who receives the file can see those numbers. Treat it like the underlying data when you share it.

## Method notes

- **Port calls:** the vessel is inside a port geofence (circle around the WPI coordinate, `crosswalks/ports.csv` radius) and accumulates at least 2 h at `sog ≤ 0.5 kn`. A silence over 6 h splits a visit. Calls cut off at the start or end of the date range are flagged `censored`. Where LA and Long Beach geofences overlap, the call goes to the nearest port centre. Anchorage wait is slow time spent within 25 km of the port, outside the berth fence, in the 72 h before arrival.
- **Allocation (`build --method`):** port-month `VES_WGT_MO`/`VES_VAL_MO` is split across dry-cargo (type 70–79) calls in proportion to `length × width × arrival draft` (`capacity`), or to the draft drop at berth (`draft_drop`, falling back to capacity × the median drop ratio). Missing dimensions are filled with the port-month median. Tankers are excluded.
- **Joins:** Census `PORT` = port of unlading ↔ crosswalk `schedule_d`. Census `CTY_CODE` is **country of origin**, not the country the goods were shipped from. It maps to Comtrade M49 through `crosswalks/countries.csv` (Taiwan = 490).
- **HHI** uses the 0–10,000 scale.

## Red flags

`build` runs a library of red-flag checks (`pacific_trade/redflags/`) and adds them to the report and to a "Red flags" section of the dashboard. **A flag is a lead for an analyst, not a finding.** Each check documents the question it asks, its method, the typology source it comes from, the data it needs, and the innocent explanations to rule out (`typology_library` table, and the check library on the dashboard). Checks whose inputs are missing are skipped and listed as such.

| ID | Area | Check | Needs |
|---|---|---|---|
| TBML-PRICE | Trade-based money laundering | Unit-price outlier (robust z of $/kg within HS6) | Census |
| TBML-MIRROR | Trade-based money laundering | Mirror-trade gap: US-reported imports vs partner-reported exports | `comtrade-pull` |
| TBML-EXCEED | Trade-based money laundering | US imports from an origin exceed that origin's total exports | Census + Comtrade |
| TBML-SURGE | Trade-based money laundering | A (minor-producer) origin's share jumps: possible transshipment | Census (+ Comtrade) |
| SANC-VESSEL | Sanctions & export controls | AIS vessel matches a list by IMO/MMSI (or name, weaker) | AIS + `lists-fetch` |
| SANC-IDENTITY | Sanctions & export controls | Identity manipulation: shared MMSI, bad IMO check digit, flag/name changes | AIS |
| SANC-DARK | Sanctions & export controls | AIS silence inside receiver coverage while the ship moved | AIS |
| SANC-COUNTRY | Sanctions & export controls | Origin under comprehensive sanctions, on a FATF list, or high Basel AML score | `crosswalks/country_risk.csv` |
| FL-DOL-GOODS | Forced labour | Good and origin on the DOL child/forced labour list | `crosswalks/dol_goods_hs.csv` |
| FL-TIP | Forced labour | Origin ranked Tier 3 in the TIP report | `crosswalks/country_risk.csv` |

Flags roll up per entity (trade flow, trade lane, origin country, vessel) as `1 - Π(1 - score)`, with repeats of one check counted once, into `red_flag_entities`.

**Reference data you maintain:**
- `python -m pacific_trade lists-fetch` downloads the OFAC SDN list and the US Consolidated Screening List (which includes the BIS Entity List). Add `--opensanctions` for the OpenSanctions consolidated file (EU, UK, UN and more); it is free for non-commercial use and needs a licence for commercial use. Put any other list, such as the DHS UFLPA Entity List, in `data/ref/lists/custom/` as a CSV with a `name` column. The download URLs were not reachable from the environment this was built in, so check them on the first run.
- `crosswalks/country_risk.csv` ships empty on purpose. Fill it from the current FATF lists, the Basel AML Index, OFAC programmes and the State Department TIP report, and record the `as_of` date. Ratings change several times a year.
- `crosswalks/dol_goods_hs.csv` maps a few DOL-listed goods to HS prefixes and is marked `verified=0`. Check it against the current DOL list.

**Screening names.** Census and Comtrade carry no company names. If you have importer, supplier or owner names (from bills of lading, a client list, a corporate registry), screen them with `python -m pacific_trade screen names.csv`. Matching is fuzzy, so every hit needs a human check against the official list.

**Adding a check.** Write a function decorated with `@typology(...)` in one of the modules (or a new one imported in `framework.run_all`), return rows built with `make_flags`, and add a test. It appears in the report, the dashboard and the library automatically.

## Open items before publishing results

1. **Verify the crosswalks.** Both seed tables have `verified=0`.
   - Ports: `wpi-fetch` overwrites coordinates from WPI. UN/LOCODEs for Nawiliwili and Kona still need to be filled from the UN/LOCODE 2025-1 release. Barbers Point is assumed to report under Census port 3201.
   - Countries: check the codes against Schedule C ("By ISO code"). `census-pull` prints any origin code in the data that has no mapping.
2. **Wildcard check.** The first keyed `census-pull` confirms whether `I_COMMODITY=8541*` works. If Census rejects it, re-run with `--no-wildcard`, which does one call per port and filters locally.
3. **Read the mode split first.** If `vessel_share` for 8541/8542 is small, the AIS side of the story only applies to 8544 cable and the minerals (recon risk #2).
4. **Guam** is AIS-only. It is outside the Census customs territory, so it has no `schedule_d` and never joins to trade data.
5. **Census disclosure suppression** removes or moves values at small ports. Missing Hawaii cells are not zeros.
6. **Reference tables beyond the three sources** (WPI, UN/LOCODE) are code lists, not data. Confirm they are acceptable under the project's "no extra data sources" rule.
