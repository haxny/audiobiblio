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
 * Human-readable text from an API error detail. FastAPI may return `detail`
 * as a string, a list (422 validation), or an object — naive interpolation
 * shows "[object Object]".
 *
 * @param {*} detail    Parsed `detail` field (any shape)
 * @param {string} [fallback]  Used when detail is empty
 * @returns {string}
 */
function errText(detail, fallback) {
  if (detail == null || detail === '') return fallback || 'Request failed';
  if (typeof detail === 'string') return detail;
  try { return JSON.stringify(detail); } catch (e) { return String(detail); }
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
    throw new Error(errText(err.detail, resp.statusText));
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

/**
 * Strip diacritics and lowercase, for eliminative filtering
 * ("cten" matches "Čtení", "CRo2" matches "cro2").
 *
 * @param {string} s
 * @returns {string}
 */
function comboNorm(s) {
  return String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

/**
 * Eliminative-search combobox (the project-wide selection pattern):
 * a text input above a list; typing narrows the list to items whose
 * label contains EVERY typed word (diacritics-insensitive). Selection
 * lists must never be bare <select> dropdowns with hundreds of options.
 *
 * @param {Object}   opts
 * @param {HTMLInputElement} opts.input    Search text input
 * @param {HTMLElement}      opts.list     Container (e.g. <ul>) for filtered items
 * @param {Array<Object>}    opts.items    Items; each needs a .label string
 * @param {function(Object): string} [opts.render]  Item → innerHTML (default: escaped label)
 * @param {function(Object): void}    opts.onSelect Called with the chosen item
 * @param {number}  [opts.maxItems=30]     Cap on rendered rows
 */
function initFilterCombo(opts) {
  const input = opts.input;
  const list = opts.list;
  const items = opts.items;
  const render = opts.render || function (it) { return escHtml(it.label); };
  const maxItems = opts.maxItems || 30;

  function show(filtered) {
    list.innerHTML = '';
    filtered.slice(0, maxItems).forEach(function (it) {
      const li = document.createElement('li');
      li.className = 'combo-item';
      li.innerHTML = render(it);
      li.addEventListener('click', function () {
        input.value = it.label;
        list.hidden = true;
        opts.onSelect(it);
      });
      list.appendChild(li);
    });
    if (filtered.length > maxItems) {
      const li = document.createElement('li');
      li.className = 'combo-item combo-more';
      li.textContent = '… a dalších ' + (filtered.length - maxItems) + ' — upřesněte hledání';
      list.appendChild(li);
    }
    if (filtered.length === 0) {
      const li = document.createElement('li');
      li.className = 'combo-item combo-more';
      li.textContent = 'Nic neodpovídá';
      list.appendChild(li);
    }
    list.hidden = false;
  }

  function filter() {
    const words = comboNorm(input.value).split(/\s+/).filter(Boolean);
    const filtered = items.filter(function (it) {
      const hay = comboNorm(it.label);
      return words.every(function (w) { return hay.indexOf(w) !== -1; });
    });
    show(filtered);
  }

  input.addEventListener('input', filter);
  input.addEventListener('focus', filter);
  document.addEventListener('click', function (e) {
    if (!list.contains(e.target) && e.target !== input) list.hidden = true;
  });
}
