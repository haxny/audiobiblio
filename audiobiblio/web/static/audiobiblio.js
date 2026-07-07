/**
 * audiobiblio.js — shared client-side helpers.
 *
 * Included from base.html so every page can call apiJson() for
 * JSON-body fetch requests (HTMX json-enc extension is not loaded).
 */

/**
 * Send a JSON request, show an alert on error, reload on success.
 *
 * @param {string} method   HTTP method ("GET", "POST", "PATCH", …)
 * @param {string} url      Request URL
 * @param {*}     [body]    Optional body — JSON-serialised when provided
 */
/**
 * Escape a value for safe interpolation into HTML strings.
 *
 * @param {*} s  Value to escape (falsy → empty string)
 * @returns {string}
 */
function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function apiJson(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: {'Content-Type': 'application/json'},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    alert(d.detail || r.statusText || 'Request failed');
    return;
  }
  location.reload();
}
