/**
 * REFERENCE COPY — NOT FOR EDITING HERE.
 *
 * This is the live "Canes Galore — GA4 + Search Console Weekly Writer"
 * Apps Script, captured 2026-09-13. It is kept in the repo because
 * gsc_query_page.gs depends on its CONFIG, writeTab_, r2 and pct, and
 * because score.py's column expectations are derived from the tabs it
 * writes. If the live script changes, update this copy.
 *
 * The authoritative version lives in the Apps Script project, not here.
 */

const CONFIG = {
  SHEET_ID: '1fYhCKWa8ES09ATPrnhgLtISJJwFiiDFId1AYE18_Lcc',
  GA4_PROPERTY: 'properties/358328482',
  GSC_SITE: 'https://www.canesgalore.com/',
  DAYS: 28,
  CONDITION_PAGE_MATCH: /(parkinson|stroke|ms-|multiple-sclerosis|neuropathy|arthritis|balance|caregiver)/i,
  TOP_N: 100,
};

function runWeekly() {
  const ss = SpreadsheetApp.openById(CONFIG.SHEET_ID);
  const { cur, prev } = dateRanges_();
  const gaChannel  = ga4Channel_(cur, prev);
  const gaLanding  = ga4Landing_(cur, prev);
  const gscQueries = gscQueries_(cur, prev);
  const gscPages   = gscPages_(cur, prev);
  writeTab_(ss, 'GA_Channels',   gaChannel);
  writeTab_(ss, 'GA_Landing',    gaLanding);
  writeTab_(ss, 'GSC_Queries',   gscQueries);
  writeTab_(ss, 'GSC_Pages',     gscPages);
  writeTab_(ss, 'Scorecard',     scorecard_(gaChannel, gaLanding, gscQueries, gscPages, cur, prev));
}

function dateRanges_() {
  const fmt = d => Utilities.formatDate(d, 'UTC', 'yyyy-MM-dd');
  const end = new Date(); end.setUTCDate(end.getUTCDate() - 3);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - (CONFIG.DAYS - 1));
  const pEnd = new Date(start); pEnd.setUTCDate(pEnd.getUTCDate() - 1);
  const pStart = new Date(pEnd); pStart.setUTCDate(pStart.getUTCDate() - (CONFIG.DAYS - 1));
  return { cur: { start: fmt(start), end: fmt(end) }, prev: { start: fmt(pStart), end: fmt(pEnd) } };
}

// --- GSC: the key constraint. ONE dimension per call, and NO pagination. ---
function gscRun_(dimension, range, limit) {
  const url = 'https://searchconsole.googleapis.com/webmasters/v3/sites/' +
              encodeURIComponent(CONFIG.GSC_SITE) + '/searchAnalytics/query';
  const body = { startDate: range.start, endDate: range.end, dimensions: [dimension],
                 rowLimit: limit || 1000, dataState: 'final' };
  const res = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(body),
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, muteHttpExceptions: true });
  const json = JSON.parse(res.getContentText());
  if (json.error) throw new Error('GSC: ' + json.error.message);
  const rows = {};
  (json.rows || []).forEach(r => { rows[r.keys[0]] = [r.clicks, r.impressions, r.ctr, r.position]; });
  return rows;
}

// --- the helpers gsc_query_page.gs depends on ---
function writeTab_(ss, name, rows) {
  const sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clearContents();
  if (rows.length) sh.getRange(1, 1, rows.length, rows[0].length).setValues(rows);
  sh.getRange(1, 1, 1, rows[0].length).setFontWeight('bold');
  sh.setFrozenRows(1);
}
const r2 = n => Math.round((Number(n) || 0) * 100) / 100;
const pct = (a, b) => b ? Math.round((a / b) * 10000) / 100 : 0;
const chg = (a, b) => b ? Math.round(((a - b) / b) * 1000) / 10 : (a ? 'NEW' : 0);
