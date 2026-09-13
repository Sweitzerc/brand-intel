/**
 * Canes Galore — Search Console query x page export
 * -------------------------------------------------
 * ADD THIS TO THE EXISTING "GA4 + Search Console Weekly Writer" PROJECT.
 * It reuses that file's CONFIG, writeTab_, r2 and pct. Paste it as a second
 * .gs file in the same project; do not create a new project.
 *
 * WHY
 * The existing gscRun_ sends dimensions: [dimension] — one dimension per
 * call. So GSC_Queries knows queries and GSC_Pages knows pages, and nothing
 * knows which queries belong to which page. That join is what the blog
 * revenue mapper needs to score buying intent per post; without it every
 * page falls back to a site-wide average.
 *
 * NO SETUP REQUIRED
 *  - webmasters.readonly is already in the project's oauthScopes.
 *  - CONFIG.GSC_SITE is already the correct property string.
 * Add a time-driven trigger for exportQueryPage, Monday, 6-7 AM.
 *
 * WHY ITS OWN TRIGGER AND NOT INSIDE runWeekly
 * Apps Script kills a single execution at 6 minutes. runWeekly already makes
 * six API calls and writes five tabs. This pull paginates and can run long,
 * so it gets its own trigger. If it were appended to runWeekly, a timeout
 * would take the five working tabs down with it.
 */

const QP = {
  TAB: 'GSC_Query_Page',
  DAYS: 90,              // GSC retains ~16 months; 28 is only what runWeekly asks for
  LAG_DAYS: 3,           // same lag runWeekly uses
  PAGE_SIZE: 25000,      // API maximum per request
  // Only pull rows for blog articles. The whole site over 90 days is a very
  // large result and the mapper only scores blog posts. Set to '' for the
  // entire property, but expect to hit the execution limit.
  PAGE_CONTAINS: '/blogs/news/',
  MAX_ROWS: 200000,      // hard stop so a runaway pull cannot hang the trigger
};

/** Pull every query x page row for the window and write it to its own tab. */
function exportQueryPage() {
  const range = qpDateRange_();
  const rows = gscQueryPage_(range);

  writeTab_(SpreadsheetApp.openById(CONFIG.SHEET_ID), QP.TAB, rows);
  Logger.log('%s: %s data rows, %s to %s', QP.TAB, rows.length - 1, range.start, range.end);
  return rows.length - 1;
}

function qpDateRange_() {
  const fmt = d => Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
  const end = new Date(); end.setUTCDate(end.getUTCDate() - QP.LAG_DAYS);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - (QP.DAYS - 1));
  return { start: fmt(start), end: fmt(end) };
}

/**
 * Both dimensions in one request, paginated.
 *
 * Deliberately a separate function rather than a change to gscRun_: that one
 * is called four times by runWeekly and returns a keyed object, and widening
 * it would mean touching working code for no gain here.
 */
function gscQueryPage_(range) {
  const url = 'https://searchconsole.googleapis.com/webmasters/v3/sites/' +
              encodeURIComponent(CONFIG.GSC_SITE) + '/searchAnalytics/query';

  const out = [['query', 'page', 'clicks', 'impressions', 'ctr', 'position']];
  let startRow = 0;

  while (out.length - 1 < QP.MAX_ROWS) {
    const body = {
      startDate: range.start,
      endDate: range.end,
      dimensions: ['query', 'page'],
      rowLimit: QP.PAGE_SIZE,
      startRow: startRow,
      type: 'web',
      dataState: 'final',
    };
    if (QP.PAGE_CONTAINS) {
      body.dimensionFilterGroups = [{
        groupType: 'and',
        filters: [{ dimension: 'page', operator: 'contains', expression: QP.PAGE_CONTAINS }],
      }];
    }

    const res = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(body),
      headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() },
      muteHttpExceptions: true,
    });

    const json = JSON.parse(res.getContentText());
    if (json.error) throw new Error('GSC query x page: ' + json.error.message);

    const batch = json.rows || [];
    batch.forEach(r => {
      out.push([r.keys[0], r.keys[1], r.clicks, r.impressions,
                pct(r.clicks, r.impressions), r2(r.position)]);
    });

    // A short page means there is nothing after it.
    if (batch.length < QP.PAGE_SIZE) break;
    startRow += QP.PAGE_SIZE;
  }

  return out;
}
