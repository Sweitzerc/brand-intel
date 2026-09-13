/**
 * Canes Galore — Search Console query x page export
 * -------------------------------------------------
 * SELF-CONTAINED. Paste into a new Apps Script project on its own, or
 * alongside the weekly writer — either works. Every identifier here is
 * QP_-prefixed precisely so it cannot collide with the weekly writer's
 * CONFIG, writeTab_, r2 or pct if the two ever share a project. Apps Script
 * puts every .gs file in one global scope, so a second `const CONFIG` is a
 * hard "Identifier has already been declared" error at load time.
 *
 * WHY THIS EXISTS
 * The weekly writer's gscRun_ sends dimensions: [dimension] — one dimension
 * per call. So GSC_Queries knows queries, GSC_Pages knows pages, and nothing
 * knows which queries belong to which page. That join is what scores buying
 * intent per blog post; without it every page falls back to a site average.
 *
 * SETUP
 *  1. Project Settings -> tick "Show appsscript.json manifest file".
 *  2. In appsscript.json, set oauthScopes to exactly:
 *       "oauthScopes": [
 *         "https://www.googleapis.com/auth/webmasters.readonly",
 *         "https://www.googleapis.com/auth/spreadsheets",
 *         "https://www.googleapis.com/auth/script.external_request"
 *       ]
 *     (If sharing the weekly writer's project, those are already present.)
 *  3. Run exportQueryPage once by hand to authorize.
 *  4. Triggers -> time-driven -> exportQueryPage, weekly, Monday, 6-7 AM.
 *     The weekly writer runs 5-6 AM and the Make watchdog at 7 AM, so this
 *     sits between them.
 *
 * You do NOT need the AnalyticsData advanced service for this file. It calls
 * Search Console only. Leaving it enabled is harmless.
 *
 * WHY ITS OWN TRIGGER RATHER THAN A CALL INSIDE runWeekly
 * Apps Script kills a single execution at 6 minutes. runWeekly already makes
 * six API calls and writes five tabs. If this pull were appended to it, a
 * timeout here would take those five working tabs down with it.
 */

const QP_CONFIG = {
  // These two MUST match the weekly writer's CONFIG. They are duplicated so
  // this file can stand alone; if the weekly writer's values ever change,
  // change them here too.
  SHEET_ID: '1fYhCKWa8ES09ATPrnhgLtISJJwFiiDFId1AYE18_Lcc',   // "Canes Galore - Reports"
  GSC_SITE: 'https://www.canesgalore.com/',                   // URL-prefix property

  TAB: 'GSC_Query_Page',
  DAYS: 90,          // GSC retains ~16 months; the writer's 28 is just its setting
  LAG_DAYS: 3,       // same lag the weekly writer uses
  PAGE_SIZE: 25000,  // API maximum rows per request
  // Only pull rows for blog articles. The whole property over 90 days is a
  // very large result and only blog posts are scored. Set to '' for
  // everything, and expect to approach the execution limit.
  PAGE_CONTAINS: '/blogs/news/',
  MAX_ROWS: 100000,  // hard stop so a runaway pull cannot hang the trigger
  WRITE_CHUNK: 5000, // rows per setValues call
};

/** Pull every query x page row for the window and write it to its own tab. */
function exportQueryPage() {
  const range = qpDateRange_();
  const rows = qpFetchQueryPage_(range);

  // Fetch fully BEFORE touching the sheet. If the API throws mid-pagination
  // we never reach the write, so the previous good tab survives intact.
  qpWriteTab_(QP_CONFIG.TAB, rows);

  const written = rows.length - 1;
  Logger.log('%s: %s rows, %s to %s', QP_CONFIG.TAB, written, range.start, range.end);
  if (written >= QP_CONFIG.MAX_ROWS) {
    // Never truncate silently: a capped pull looks identical to a complete
    // one once it is sitting in a tab.
    Logger.log('WARNING hit MAX_ROWS (%s). The export is TRUNCATED and the ' +
               'tail is missing. Raise MAX_ROWS or narrow PAGE_CONTAINS.',
               QP_CONFIG.MAX_ROWS);
  }
  return written;
}

function qpDateRange_() {
  const fmt = d => Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
  const end = new Date(); end.setUTCDate(end.getUTCDate() - QP_CONFIG.LAG_DAYS);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - (QP_CONFIG.DAYS - 1));
  return { start: fmt(start), end: fmt(end) };
}

/** Both dimensions in one request, paginated. keys[] follows `dimensions`. */
function qpFetchQueryPage_(range) {
  const url = 'https://searchconsole.googleapis.com/webmasters/v3/sites/' +
              encodeURIComponent(QP_CONFIG.GSC_SITE) + '/searchAnalytics/query';

  const out = [['query', 'page', 'clicks', 'impressions', 'ctr', 'position']];
  let startRow = 0;

  while (out.length - 1 < QP_CONFIG.MAX_ROWS) {
    const body = {
      startDate: range.start,
      endDate: range.end,
      dimensions: ['query', 'page'],
      rowLimit: QP_CONFIG.PAGE_SIZE,
      startRow: startRow,
      type: 'web',
      dataState: 'final',
    };
    if (QP_CONFIG.PAGE_CONTAINS) {
      body.dimensionFilterGroups = [{
        groupType: 'and',
        filters: [{ dimension: 'page', operator: 'contains', expression: QP_CONFIG.PAGE_CONTAINS }],
      }];
    }

    const res = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(body),
      headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() },
      muteHttpExceptions: true,
    });

    const code = res.getResponseCode();
    const text = res.getContentText();
    if (code !== 200) {
      // muteHttpExceptions means a 403 or 429 arrives as a normal response.
      // Without this check the JSON.parse below would mask it.
      throw new Error('GSC query x page HTTP ' + code + ': ' + text.slice(0, 500));
    }
    const json = JSON.parse(text);
    if (json.error) throw new Error('GSC query x page: ' + json.error.message);

    const batch = json.rows || [];
    batch.forEach(r => {
      out.push([
        r.keys[0],                                  // query
        r.keys[1],                                  // page, a full URL
        r.clicks,
        r.impressions,
        qpPct_(r.clicks, r.impressions),            // percent, like the other tabs
        qpR2_(r.position),
      ]);
    });

    if (batch.length < QP_CONFIG.PAGE_SIZE) break;  // a short page ends it
    startRow += QP_CONFIG.PAGE_SIZE;
  }

  return out;
}

/**
 * Write a 2D array to a tab, chunked.
 *
 * Deliberately not the weekly writer's writeTab_: that one does a single
 * setValues for the whole array, which is fine for its 100-row tabs but not
 * for a 90-day query x page pull that can run to tens of thousands of rows.
 */
function qpWriteTab_(name, rows) {
  const ss = SpreadsheetApp.openById(QP_CONFIG.SHEET_ID);
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clear();  // clear(), not clearContents(): also drops stale formatting
  if (!rows.length) return;

  const width = rows[0].length;
  for (let i = 0; i < rows.length; i += QP_CONFIG.WRITE_CHUNK) {
    const slice = rows.slice(i, i + QP_CONFIG.WRITE_CHUNK);
    sh.getRange(1 + i, 1, slice.length, width).setValues(slice);
    SpreadsheetApp.flush();
  }
  sh.getRange(1, 1, 1, width).setFontWeight('bold');
  sh.setFrozenRows(1);
}

const qpR2_ = n => Math.round((Number(n) || 0) * 100) / 100;
const qpPct_ = (a, b) => b ? Math.round((a / b) * 10000) / 100 : 0;
