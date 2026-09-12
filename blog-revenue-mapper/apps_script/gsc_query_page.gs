/**
 * Search Console query x page export, for the Canes Galore reports sheet.
 *
 * WHY THIS EXISTS
 * The reports sheet carries a trailing 28 days because that is the range the
 * Monday script asks for. Search Console retains ~16 months. It also exports
 * queries and pages as two independent lists, so there is no way to say which
 * queries belong to which page. This adds both dimensions over any window.
 *
 * WHY IT RUNS HERE AND NOT IN PYTHON
 * A bound Apps Script runs as YOU, using your own Google authorisation. It
 * needs no service account and no downloaded key file. The account that owns
 * this sheet already owns the Search Console property, so it already has
 * access. There is nothing to provision.
 *
 * SETUP, ONE TIME
 * 1. Paste this file into the existing Monday script project.
 * 2. Project Settings -> tick "Show appsscript.json manifest file".
 * 3. In appsscript.json add to oauthScopes:
 *      "https://www.googleapis.com/auth/webmasters.readonly"
 *    Keep the scopes already there.
 * 4. Run listSearchConsoleSites() once. It logs every property you can read,
 *    with the EXACT siteUrl string. Copy the right one into SITE_URL below.
 *    A URL-prefix property looks like  https://www.canesgalore.com/
 *    A domain property looks like      sc-domain:canesgalore.com
 *    Guessing wrong returns 403, which looks exactly like a permissions
 *    problem, so read it from this list rather than assuming.
 * 5. Run exportQueryPage(). It writes the GSC_Query_Page tab.
 *
 * Read-only. It calls no write endpoint and touches nothing but its own tab.
 */

var SITE_URL = 'https://www.canesgalore.com/';  // verify with listSearchConsoleSites()
var TAB_NAME = 'GSC_Query_Page';
var WINDOW_DAYS = 90;
var LAG_DAYS = 3;          // Search Console data is incomplete for ~3 days
var PAGE_SIZE = 25000;     // API maximum per request
var API = 'https://searchconsole.googleapis.com/webmasters/v3';

function authHeaders_() {
  return { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() };
}

/** Log every property this account can read, with its exact siteUrl. */
function listSearchConsoleSites() {
  var response = UrlFetchApp.fetch(API + '/sites', {
    headers: authHeaders_(),
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('sites.list failed ' + response.getResponseCode() + ': ' +
                    response.getContentText());
  }
  var entries = JSON.parse(response.getContentText()).siteEntry || [];
  if (!entries.length) {
    Logger.log('No Search Console properties are readable by this account.');
    return entries;
  }
  Logger.log('Copy the siteUrl you want into SITE_URL:');
  entries.forEach(function (entry) {
    Logger.log('  %s   (%s)', entry.siteUrl, entry.permissionLevel);
  });
  return entries;
}

function isoDaysAgo_(days) {
  var date = new Date();
  date.setDate(date.getDate() - days);
  return Utilities.formatDate(date, 'UTC', 'yyyy-MM-dd');
}

/**
 * Pull every query x page row for the window and write it to TAB_NAME.
 * Paginates until Search Console stops returning full pages.
 */
function exportQueryPage() {
  var endDate = isoDaysAgo_(LAG_DAYS);
  var startDate = isoDaysAgo_(LAG_DAYS + WINDOW_DAYS - 1);
  var url = API + '/sites/' + encodeURIComponent(SITE_URL) + '/searchAnalytics/query';

  var rows = [];
  var startRow = 0;

  while (true) {
    var response = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: authHeaders_(),
      muteHttpExceptions: true,
      payload: JSON.stringify({
        startDate: startDate,
        endDate: endDate,
        dimensions: ['query', 'page'],
        rowLimit: PAGE_SIZE,
        startRow: startRow,
        type: 'web',
        dataState: 'final'
      })
    });

    var code = response.getResponseCode();
    if (code === 403) {
      throw new Error(
        '403 from Search Console. Either SITE_URL is not the exact property ' +
        'string (run listSearchConsoleSites) or this account cannot read it. ' +
        'Body: ' + response.getContentText());
    }
    if (code !== 200) {
      throw new Error('searchAnalytics.query failed ' + code + ': ' +
                      response.getContentText());
    }

    var batch = JSON.parse(response.getContentText()).rows || [];
    batch.forEach(function (row) {
      rows.push([
        row.keys[0],                        // query
        row.keys[1],                        // page
        row.clicks || 0,
        row.impressions || 0,
        Math.round((row.ctr || 0) * 1000000) / 10000,   // percent
        Math.round((row.position || 0) * 100) / 100
      ]);
    });

    if (batch.length < PAGE_SIZE) break;
    startRow += PAGE_SIZE;
  }

  writeTab_(TAB_NAME,
            ['query', 'page', 'clicks', 'impressions', 'ctr', 'position'],
            rows);

  Logger.log('%s: %s rows, %s to %s', TAB_NAME, rows.length, startDate, endDate);
  return rows.length;
}

function writeTab_(name, header, rows) {
  var book = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = book.getSheetByName(name) || book.insertSheet(name);
  sheet.clear();
  sheet.getRange(1, 1, 1, header.length).setValues([header]);
  if (rows.length) {
    // Chunked so a large export does not blow the per-call payload limit.
    var CHUNK = 5000;
    for (var i = 0; i < rows.length; i += CHUNK) {
      var slice = rows.slice(i, i + CHUNK);
      sheet.getRange(2 + i, 1, slice.length, header.length).setValues(slice);
    }
  }
  sheet.setFrozenRows(1);
}
