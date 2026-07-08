/**
 * audiobiblio.js — shared client-side helpers.
 *
 * Included from base.html so every page can call apiJson() for
 * JSON-body fetch requests (HTMX json-enc extension is not loaded).
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

/**
 * Send a JSON request, show an alert on error, reload on success.
 *
 * @param {string} method   HTTP method ("GET", "POST", "PATCH", …)
 * @param {string} url      Request URL
 * @param {*}     [body]    Optional body — JSON-serialised when provided
 */
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

/**
 * Finalize preview/apply — data-returning fetch (NOT apiJson: apiJson reloads
 * the page, which would kill the preview flow).
 * First click shows the dry-run plan; Apply re-posts with dry_run=false.
 */
let finalizeWorkId = null;

async function finalizeCall(workId, dryRun) {
  const resp = await fetch(`/api/v1/works/${workId}/finalize`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({dry_run: dryRun}),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

async function finalizePreview(workId) {
  finalizeWorkId = workId;
  const panel = document.getElementById('finalize-panel');
  const pre = document.getElementById('finalize-actions');
  const errBox = document.getElementById('finalize-error');
  panel.hidden = false;
  errBox.textContent = '';
  pre.textContent = 'Loading preview…';
  try {
    const data = await finalizeCall(workId, true);
    pre.textContent = data.actions.join('\n');
    if (data.errors && data.errors.length) {
      errBox.textContent = 'Warnings: ' + data.errors.join('; ');
    }
  } catch (e) {
    pre.textContent = '';
    errBox.textContent = 'Error: ' + e.message;
  }
}

async function finalizeApply() {
  if (finalizeWorkId === null) return;
  const pre = document.getElementById('finalize-actions');
  const errBox = document.getElementById('finalize-error');
  try {
    const data = await finalizeCall(finalizeWorkId, false);
    if (data.errors && data.errors.length) {
      errBox.textContent = 'Completed with warnings: ' + data.errors.join('; ');
      pre.textContent = data.actions.join('\n');
    } else {
      window.location.reload();
    }
  } catch (e) {
    errBox.textContent = 'Error: ' + e.message;
  }
}
