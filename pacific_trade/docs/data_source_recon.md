# Data Source Recon: Pacific semiconductor & critical-mineral shipments

**Prepared:** 2026-10-19 (HST) · **Scope:** NOAA Marine Cadastre AIS, UN Comtrade, Census USA Trade Online / International Trade API. No other sources.
**Commodities:** HS 8541–8544 (semiconductors / ICs / wire & cable), HS 2609–2621 + 2846 (critical-mineral ores, concentrates, rare-earth compounds).

Tags: **[TESTED]** means I hit it live from the box with curl or Python on 2026-10-19. **[DOC]** means taken from the cited documentation.

---

## TL;DR verdicts

| Source | Verdict | Auth | Latest data | Grain |
|---|---|---|---|---|
| Marine Cadastre AIS | **GO**, with caveats about coverage | None | Daily points through **2026-06-30** | 1-min vessel positions, U.S. coastal waters only (terrestrial receivers) |
| UN Comtrade | **GO** for country-level context | Free key (500 calls/day) | US annual **2025**; US monthly **2026-07** | Reporter × partner × HS6 × month/year |
| Census Intl Trade API (`porths`, `hs`) | **GO, and it's the core trade table**. A key is now **mandatory** | Free key (required) | Monthly, 2010→present (could not confirm the latest month without a key) | Port × HS6 × country × month, with vessel value/weight (`porths`); District × HS10 × country (`hs`) |

**The central limitation:** none of the three sources links a specific vessel (IMO/MMSI) to a specific commodity. The fusion is a **port × month** join (Census port codes ↔ UN/LOCODE ↔ AIS port-call geofences), plus a **country × HS6** join to Comtrade. Vessel-level commodity attribution can only be **estimated by allocation**, not observed.

---

## 1. NOAA Marine Cadastre AIS

### Access
- **Bulk daily files (current, reprocessed "csv2")** [TESTED]:
  `https://noaaocm.blob.core.windows.net/ais/csv2/csv{YYYY}/ais-{YYYY}-{MM}-{DD}.csv.zst`
  Index pages: `https://noaaocm.blob.core.windows.net/ais/csv2/csv2026/index.html` (181 files, 2026-01-01 → 2026-06-30), plus `csv2025`, `csv2024`, and so on (2024 index lists 366 files).
  Source: hub page https://hub.marinecadastre.gov/pages/vesseltraffic (lists 2009–2026 file lists); InPort 2025 record https://www.fisheries.noaa.gov/inport/item/77594
- **Legacy path** (`https://coast.noaa.gov/htdata/CMSP/AISDataHandler/{YYYY}/AIS_{YYYY}_{MM}_{DD}.zip`) still serves ≤2024 zips [TESTED]. `…/2025/` now returns **404** [TESTED]. Use the blob path.
- **AccessAIS** (custom area + date order, emailed link valid 14 days / 5 downloads; it points to bulk download for large orders): https://marinecadastre.gov/accessais/, help PDF https://coast.noaa.gov/data/marinecadastre/ais/accessais-help.pdf. Fine for a quick Hawaii pull, but scripted bulk download is more reproducible.
- **Derived products:** monthly vessel tracks (GeoParquet for 2024/2025) at https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais/aistrack/index-aistrack.html; transit counts at https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/ais/aistransit/index-aistransit.html (linked from the hub page). The hub page also says 2024 broadcast points are available as GeoParquet via their GitHub (https://github.com/ocm-marinecadastre/ais-vessel-traffic). I did not verify the Parquet schema.
- **Auth / rate limit:** none (public Azure blob; InPort says "Access Constraints: None").

### Coverage, format, size
- Years: 2009–2014 monthly FGDB by UTM zone; 2015→ daily national CSV. **Latest: 2026-01-01 to 2026-06-30** (hub summary updated 2026-09-18).
- Sizes (hub summary table): 2025 = 365 files, 3.0 B records, **81.5 GB compressed / ~326 GB raw**. 2026 H1 = 181 files, 41.1 GB.
  Per-day `.zst` from the 2026 index [TESTED]: 2026-01-01 **228.2 MB**, 2026-04-10 **201.5 MB**, 2026-06-30 **306.5 MB**. Legacy 2024 zip (AIS_2024_07_15.zip) is **~396 MB** [TESTED].
- **Pacific/Hawaii zone options:** since 2015 there are no zone files. Each daily file is national (the InPort extent is 141°E to 60°W, 10°S to 51.5°N). You have to filter Hawaii / West Coast / Guam yourself. The hub page says a Guam-only direct download exists for 2015–2025. UTM zones only apply to 2009–2014.
- **Coverage limits** [DOC, FAQ https://coast.noaa.gov/data/marinecadastre/ais/faq.pdf]:
  - Land-based receivers only, **no satellite AIS**.
  - Coverage is "unavailable for remote Pacific territories, foreign waters, or waters extending more than 40 to 50 miles from the coast."
  - **Alaska points were removed after 2021-03-28.**
  - So you can't see trans-Pacific routes, only approaches and port areas.
- **One-day probe** [TESTED, `ais_probe.py`, file `ais-2026-06-15.csv.zst`, streamed and parsed in 23 s on the box]:
  - 9,349,447 rows; `imo` populated on 34.9% of rows.
  - Hawaii bbox (lon −161…−154, lat 18…23): 62,926 rows, 172 distinct MMSIs, and only **16 distinct IMOs with vessel_type 70–89 (cargo/tanker)**.
  - Guam bbox: 11,256 rows.
  - Top Hawaii vessel_type codes: 30 (fishing), 52 (tug), 36, 37, 70 (cargo), 32, 80 (tanker).

### Exact columns
- **2025+ csv2 schema (snake_case)** [TESTED header of ais-2026-06-15]:
  `mmsi, base_date_time, longitude, latitude, sog, cog, heading, vessel_name, imo, call_sign, vessel_type, status, length, width, draft, cargo, transceiver`
- **Legacy ≤2024 zip schema** [TESTED header of AIS_2023_07_15.csv]:
  `MMSI, BaseDateTime, LAT, LON, SOG, COG, Heading, VesselName, IMO, CallSign, VesselType, Status, Length, Width, Draft, Cargo, TransceiverClass`
- InPort documents the 2025 changes: new field case and format, lon/lat order swapped, sentinel values set to null, `transceiver` shortened, invalid MMSIs removed. Vessel attributes are validated against the AIS Vessel Identification Database (AVID). **Pipeline must normalise both schemas.**
- `imo` carries an `IMO` prefix (e.g. `IMO9267209`). Strip it before joining. Times are UTC.
- **No commodity, cargo manifest, origin or destination port.** The `cargo` field is the AIS ship/cargo-type code, not what's on board.

---

## 2. UN Comtrade

### Access
- **Keyless preview** [TESTED]: `https://comtradeapi.un.org/public/v1/preview/C/{A|M}/HS?reporterCode=842&partnerCode=490&period=2024&cmdCode=8542&flowCode=M` returned HTTP 200 with a full record. Limit is 500 records and one period/product per call, at 1 call/s (https://uncomtrade.org/docs/un-comtrade-api/, https://uncomtrade.org/docs/subscriptions/).
- **Keyless availability** [TESTED]: `https://comtradeapi.un.org/public/v1/getDA/C/A/HS?reporterCode=842&period=2025`
- **Keyed data API:** `https://comtradeapi.un.org/data/v1/get/C/{A|M}/HS?...&subscription-key=KEY` (example on https://uncomtrade.org/docs/un-comtrade-api/).
- **Key:** register a free account, then generate a key in My API Portal https://comtradeplus.un.org/MyComtrade/APIPortal (https://uncomtrade.org/docs/api-subscription-keys/). Free keys "may be periodically regenerated".
- **Limits (free):** 500 calls/day, 1 call/s, up to 100k records/call. Bulk files and async downloads are Premium only (https://comtradeplus.un.org/Subscriptions).

### Coverage
- [TESTED getDA] US annual 2025: first released 2026-04-22, 1,191,046 records, classification H6.
- US monthly 2026-07: released 2026-09-09.
- Sample monthly pull [TESTED]: US imports of HS 2846 from China (partner 156), 2026-07: primaryValue $13,456,650, netWgt 343,919 kg.
- Commodity detail is **HS6 maximum** in this API (national tariff lines are a separate `getTariffline` endpoint per docs). For semiconductors, HS6 is enough.

### Exact fields (from a live record) [TESTED]
`typeCode, freqCode, refPeriodId, refYear, refMonth, period, reporterCode, reporterISO, reporterDesc, flowCode, flowDesc, partnerCode, partnerISO, partnerDesc, partner2Code, partner2ISO, partner2Desc, classificationCode, classificationSearchCode, isOriginalClassification, cmdCode, cmdDesc, aggrLevel, isLeaf, customsCode, customsDesc, mosCode, motCode, motDesc, qtyUnitCode, qtyUnitAbbr, qty, isQtyEstimated, altQtyUnitCode, altQtyUnitAbbr, altQty, isAltQtyEstimated, netWgt, isNetWgtEstimated, grossWgt, isGrossWgtEstimated, cifvalue, fobvalue, primaryValue, legacyEstimationFlag, isReported, isAggregate`
- The preview returns `*Desc`/`*ISO` as null. Map codes with the reference files (e.g. https://comtradeapi.un.org/files/v1/app/reference/ModeOfTransportCodes.json [TESTED]).
- Country codes are **M49 numeric**: 842 = USA, 156 = China, 490 = "Other Asia, nes" (Taiwan).
- **Mode of transport:** `motCode` exists (2100 = Sea, 1000 = Air per the reference file). However, for US, Taiwan (490) and Korea (410) as reporters on HS 8542 in 2024, only `motCode=0` (all modes) came back, and filtering `motCode=2100` returned the same total [TESTED]. **Don't count on a sea-only split from Comtrade.** Use Census `VES_VAL_*` for that.

---

## 3. Census: International Trade API + USA Trade Online

### Access & auth
- **The API key is now mandatory.** Every keyless request I made redirected (302) to `https://api.census.gov/data/missing_key.html` [TESTED]. Census says "All data queries to the Census Data API now require an API key" (https://www.census.gov/data/developers/data-sets/international-trade.html; announcement video dated 2026-05-11: https://www.census.gov/library/video/2026/adrm/requesting-a-census-data-api-key.html).
- **Get a key:** https://api.census.gov/data/key_signup.html (free, email activation), then append `&key=KEY`.
- **Limits:** max **50 variables per query**. The documented 500-queries/day/IP limit is for keyless use, and keys lift it (https://www.census.gov/data/developers/guidance/api-user-guide.Query_Limits.html). Census has not published a hard per-key quota that I could find.
- Metadata endpoints still work without a key [TESTED]: `…/variables.json`, the dataset catalog `https://api.census.gov/data/timeseries/intltrade.json`.
- **USA Trade Online** (web UI, no login needed; an email PIN is only for saving reports, per https://www.census.gov/foreign-trade/data/USA_Trade_Online_Reimagined_User_Guide.pdf). Good for checking API pulls by hand. Not needed in the pipeline because the API exposes the same port/HS6 cube.

### Endpoints that matter
**A. `https://api.census.gov/data/timeseries/intltrade/imports/porths`: port × HS × country × month, with vessel value.** Yes, it exposes port-level HS data with vessel value and weight. Exact variables [TESTED via variables.json]:
`PORT, PORT_NAME, I_COMMODITY, I_COMMODITY_LDESC, I_COMMODITY_SDESC, COMM_LVL, CTY_CODE, CTY_NAME, GEN_VAL_MO, GEN_VAL_YR, VES_VAL_MO, VES_VAL_YR, VES_WGT_MO, VES_WGT_YR, CNT_VAL_MO, CNT_VAL_YR, CNT_WGT_MO, CNT_WGT_YR, AIR_VAL_MO, AIR_VAL_YR, AIR_WGT_MO, AIR_WGT_YR, SUMMARY_LVL, SUMMARY_LVL2, LAST_UPDATE, YEAR, MONTH, time`
- `COMM_LVL` = HS2 / HS4 / HS6 only. **No HS10 at port level.** This matches USATO, where port-level data is 6-digit HS, 2003–present (https://www2.census.gov/programs-surveys/trade/data/tradedata.html).
- There is also a BETA twin, `imports/porthsimport`, with extra geography fields (`CUSTDISTRICT, US_PORT, GEO_ID, NATION, USITC, USITCREG, WORLD, ucgid`, …) and temporal 2010-01/2030-12 [TESTED variables].
- Example (needs key): `https://api.census.gov/data/timeseries/intltrade/imports/porths?get=PORT,PORT_NAME,I_COMMODITY,CTY_CODE,CTY_NAME,GEN_VAL_MO,VES_VAL_MO,VES_WGT_MO,CNT_VAL_MO,AIR_VAL_MO&time=2026-06&COMM_LVL=HS6&I_COMMODITY=8542*&key=KEY`. Untested because of the key. Wildcard support on `I_COMMODITY` should be confirmed on the first keyed call.

**B. `https://api.census.gov/data/timeseries/intltrade/imports/hs`: district × HS10 × country × month.** Variables include [TESTED]: `DISTRICT, DIST_NAME, I_COMMODITY (COMM_LVL up to HS10), CTY_CODE, CTY_NAME, CTY_SUBCODE, GEN_VAL_MO, CON_VAL_MO, GEN_CIF_MO, VES_VAL_MO, VES_WGT_MO, VES_CHA_MO, CNT_VAL_MO, AIR_VAL_MO, GEN_QY1_MO, CON_QY1_MO, UNIT_QY1, UNIT_QY2, DUT_VAL_MO, CAL_DUT_MO, RP, …` (+ `_YR` variants). Use it when you need HS10 (e.g. 8541.10 vs 8541.4x subtypes, or rare-earth 2846 splits), at **customs-district** grain.

### Semantics that affect the joins [DOC, https://www.census.gov/foreign-trade/reference/guides/methodology/usatradeonline-port-tech.html]
- Import `PORT` = **port of unlading** (where the cargo comes off the vessel). That's the right concept to match against AIS port calls.
- `CTY_CODE` for imports = **country of origin** (where the goods were made or mined), **not** the country or port of lading. A Taiwanese chip transshipped via Busan shows up as Taiwan.
- **Disclosure suppression:** ports can be combined under arbitrary designations, and value/weight can be dropped or moved to other ports. Expect gaps for small Hawaii ports and narrow HS6 codes.
- Shipping weight exists only for air and vessel. Vessel/air values won't necessarily sum to the total.
- Annual revisions are published with the April statistics (developers page).

### Port codes (Schedule D)
- Lists: https://www.census.gov/foreign-trade/schedules/d/distcode.html; TXT/ASCII files at https://www2.census.gov/programs-surveys/trade/reference/codes/index.html
- Parsed from the HTML [TESTED]: District 32 HONOLULU, HI; 3201 HONOLULU; 3202 HILO; 3203 KAHULUI; 3204 NAWILIWILI-PORT ALLEN; 3205 HONOLULU INTL AIRPRT; 3206 KONA. Also 2704 LOS ANGELES; 2709 LONG BEACH; 2809 SAN FRANCISCO; 2811 OAKLAND; 3001 SEATTLE; 3002 TACOMA.
- Country codes: Schedule C, which includes a "By ISO code" list on the same reference page. You need it to map Census `CTY_CODE` → ISO → Comtrade M49.

---

## 4. What joins and what doesn't

| Pair | Direct key? | Reality |
|---|---|---|
| AIS ↔ Census | **No.** Census has no vessel, IMO, MMSI or voyage | Join on **port × month** only, via a port-code crosswalk + geofence |
| AIS ↔ Comtrade | **No** | Comtrade has no port or vessel. Only indirect, through Census |
| Census ↔ Comtrade | **Partial** | **HS6 + country + month/year.** Census `CTY_CODE` (Schedule C) → ISO → M49. Census value is US import (customs/CIF); Comtrade reporter=842 flow M should roughly match. Partner-reported US exports give the mirror |
| AIS internal | IMO / MMSI | `imo` is null on ~65% of rows (mostly small craft). Resolve on MMSI ↔ IMO ↔ call_sign ↔ vessel_name over time. Keep cargo and tanker types (70–89) |
| Commodity → vessel | **Not observable** | Neither source has bills of lading. Attribution must be modelled |

### Proposed linkage (within scope)
1. **Port crosswalk table** (hand-built, ~20–40 Pacific ports): `schedule_d_port (4-char) → unlocode (5-char) → wpi_number → lat/lon → geofence polygon`.
   - **World Port Index** (NGA Pub 150) CSV: `https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv` [TESTED: 3,807 ports, 3.5 MB]. It includes the columns `World Port Index Number, Main Port Name, UN/LOCODE, Country Code, Latitude, Longitude`, plus depth and max-vessel fields.
   - Hawaii examples from WPI [TESTED]: Honolulu `US HNL`, Barber's Point `US NAX`, Kahului `US OGG`, Hilo `US ITO`.
   - **UN/LOCODE** official release 2025-1 (CSV): https://unece.org/trade/cefact/UNLOCODE-Download
   - Schedule D has no UN/LOCODE column, so this mapping is a manual, documented step. That's fine for a few dozen ports.
   - Strictly, WPI and UN/LOCODE add two reference tables beyond your three sources. They're code lists, not new data, but confirm this with your advisor given the "no extra data sources" rule.
2. **AIS port calls:** build WPI-point buffers or simple polygons. Detect port-call events as entry → `sog`<~0.5 kn dwell → exit, giving `(imo/mmsi, port, arrival, departure, dwell_h, vessel_type, draft)`. Aggregate to `port × month × vessel_type`.
3. **Fused fact table:** `port × month × HS6 × origin_country` (Census VES_VAL/VES_WGT/CNT_VAL) ⨝ `port × month` AIS call stats ⨝ `origin_country × HS6 × year` Comtrade (exporter-side concentration, e.g. world-export HHI by supplier).
4. **Vessel attribution (modelled, label it as such):** allocate port-month `VES_WGT_MO` to calling cargo vessels in proportion to a capacity proxy (length × width × draft change). This lets the vessel ↔ port ↔ commodity graph carry *estimated* weights.
5. **Risk metrics feasible from these grains:** HHI over origin countries per port × HS6 (Census) and over global exporters (Comtrade); port concentration HHI of US vessel imports per HS6; AIS dwell-time mean/variance per port-month; and "route deviation" **only within coastal receiver range** (approach lanes, anchorage waits), not ocean legs.

---

## 5. Top risks / gaps
1. **No vessel ↔ cargo link anywhere in scope.** Census is port × HS6 × origin country. AIS is vessel × position. Every vessel-level single-point-of-failure claim is an allocation model, not an observation. Present it that way. (CBP manifest data would be the fix, but it's out of scope.)
2. **Semiconductors may mostly be air freight.** HS 8541/8542 are high value per kg. Check `VES_VAL_MO` vs `AIR_VAL_MO` on the first keyed `porths` pull. If the vessel share is small, the AIS story fits better for **8544 (cable)** and the **minerals (26xx, 2846)**. Critical-mineral flows into Hawaii specifically may be close to zero or suppressed. The West Coast ports (2704/2709/2811/3001/3002) are where the volume is.
3. **AIS coverage is coastal-only and Hawaii is thin.** Terrestrial receivers reach ~40–50 mi, there's no satellite, Alaska was removed after 2021-03, and remote Pacific territories aren't covered. The 1-day Hawaii probe saw only 16 cargo/tanker IMOs. Also plan for volume: ~80 GB/yr compressed. Stream-filter to a Pacific bbox and write Parquet, which is ~25 s/day on the box. And handle the 2024→2025 schema change.
   - Minor issues: Census disclosure suppression at small ports; `CTY_CODE` is origin, not lading country; Comtrade free keys can be regenerated, so keep keys in env vars; annual Census revisions every April.

## 6. Keys to register
- **Census Data API key (required):** https://api.census.gov/data/key_signup.html
- **UN Comtrade free API key (recommended):** create an account, then My API Portal https://comtradeplus.un.org/MyComtrade/APIPortal (the preview works keyless but caps at 500 records/call)
- **AIS / WPI / Schedule D / UN/LOCODE:** no key needed.

## Artifacts on box
- `/workspace/practicum/ais_probe.py`: streaming AIS day probe (zstandard + csv), with output in `ais_probe.out`
- `/workspace/practicum/wpi.csv`: World Port Index download
- `/workspace/practicum/pds.pdf, faq.pdf, aah.pdf, sd.html`: cached docs
