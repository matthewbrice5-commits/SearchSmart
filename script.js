const API_URL = 'https://vvyzyjkemj.execute-api.us-east-2.amazonaws.com/';

const PRICE_TEXT = {
  any: 'Any',
  budget: '$',
  mid: '$$',
  premium: '$$$',
};

const MODE_TEXT = {
  both: 'Both',
  online: 'Online',
  local: 'Local',
};

const filters = {
  count: 5,
  price: 'any',
  mode: 'both',
};

let isSearching = false;

const app = document.getElementById('app');
const form = document.getElementById('search-form');
const input = document.getElementById('q');
const submit = document.getElementById('submit');
const submitLabel = document.getElementById('submit-label');
const logo = document.getElementById('logo');
const tagline = document.getElementById('tagline');
const examples = document.getElementById('examples');
const results = document.getElementById('results');
const filtersToggle = document.getElementById('filters-toggle');
const filtersPanel = document.getElementById('filters-panel');
const filtersSummary = document.getElementById('filters-summary');
const nearRow = document.getElementById('near-row');
const nearInput = document.getElementById('near');
const historyToggle = document.getElementById('history-toggle');
const historyPanel = document.getElementById('history-panel');
const historyList = document.getElementById('history-list');
const historyClear = document.getElementById('history-clear');

/* ---------- Filters dropdown ---------- */

function setPanelOpen(open) {
  if (open) setHistoryOpen(false);
  filtersPanel.hidden = !open;
  filtersToggle.setAttribute('aria-expanded', String(open));
}

filtersToggle.addEventListener('click', () => setPanelOpen(filtersPanel.hidden));

document.addEventListener('click', (e) => {
  if (
    !filtersPanel.hidden &&
    !filtersPanel.contains(e.target) &&
    !filtersToggle.contains(e.target)
  ) {
    setPanelOpen(false);
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !filtersPanel.hidden) {
    setPanelOpen(false);
    filtersToggle.focus();
  }
  if (e.key === 'Escape' && !historyPanel.hidden) {
    setHistoryOpen(false);
    historyToggle.focus();
  }
});

/* ---------- Search history (stored in this browser only) ---------- */

const HISTORY_KEY = 'searchsmart-history';
const HISTORY_MAX = 20;

function setHistoryOpen(open) {
  if (open) setPanelOpen(false);
  historyPanel.hidden = !open;
  historyToggle.setAttribute('aria-expanded', String(open));
}

historyToggle.addEventListener('click', () => setHistoryOpen(historyPanel.hidden));

document.addEventListener('click', (e) => {
  // composedPath() is fixed at dispatch time, so clicking a row that re-renders
  // the list (remove) still counts as a click inside the panel.
  const path = e.composedPath();
  if (!historyPanel.hidden && !path.includes(historyPanel) && !path.includes(historyToggle)) {
    setHistoryOpen(false);
  }
});

function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(list)
      ? list.filter((x) => x && typeof x.q === 'string' && Number.isFinite(x.t))
      : [];
  } catch {
    return [];
  }
}

function saveHistory(list) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  } catch {
    /* storage blocked: history just won't persist */
  }
}

function addHistory(query) {
  const key = query.toLowerCase();
  const list = loadHistory().filter((x) => x.q.toLowerCase() !== key);
  list.unshift({ q: query, t: Date.now() });
  saveHistory(list.slice(0, HISTORY_MAX));
  renderHistory();
}

function timeAgo(t) {
  const secs = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function renderHistory() {
  const list = loadHistory();
  historyClear.hidden = list.length === 0;

  if (list.length === 0) {
    historyList.replaceChildren(el('li', 'history-empty', 'No searches yet.'));
    return;
  }

  historyList.replaceChildren(
    ...list.map((entry, i) => {
      const li = el('li', 'history-row');

      const item = el('button', 'history-item');
      item.type = 'button';
      item.dataset.index = String(i);
      item.append(el('span', 'history-query', entry.q), el('span', 'history-time', timeAgo(entry.t)));

      const remove = el('button', 'history-remove', '\u00d7');
      remove.type = 'button';
      remove.dataset.index = String(i);
      remove.setAttribute('aria-label', `Remove "${entry.q}" from history`);

      li.append(item, remove);
      return li;
    })
  );
}

historyList.addEventListener('click', (e) => {
  const list = loadHistory();

  const remove = e.target.closest('.history-remove');
  if (remove) {
    list.splice(Number(remove.dataset.index), 1);
    saveHistory(list);
    renderHistory();
    return;
  }

  const item = e.target.closest('.history-item');
  if (!item) return;
  const entry = list[Number(item.dataset.index)];
  if (!entry) return;
  setHistoryOpen(false);
  input.value = entry.q;
  updateSubmitState();
  search(entry.q);
});

historyClear.addEventListener('click', () => {
  saveHistory([]);
  renderHistory();
});

document.querySelectorAll('.toggle-group').forEach((group) => {
  const key = group.dataset.filter;

  group.addEventListener('click', (e) => {
    const button = e.target.closest('button');
    if (!button) return;

    group
      .querySelectorAll('button')
      .forEach((b) => b.setAttribute('aria-pressed', String(b === button)));

    filters[key] = key === 'count' ? Number(button.dataset.value) : button.dataset.value;
    updateSummary();
  });
});

function updateSummary() {
  filtersSummary.textContent = `${filters.count} results · ${PRICE_TEXT[filters.price]} · ${MODE_TEXT[filters.mode]}`;
  nearRow.hidden = filters.mode === 'online';
}

/* ---------- Search ---------- */

input.addEventListener('input', updateSubmitState);

function updateSubmitState() {
  submit.disabled = isSearching || input.value.trim().length < 2;
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  search(input.value);
});

examples.addEventListener('click', (e) => {
  const button = e.target.closest('button');
  if (!button) return;
  input.value = button.textContent;
  search(button.textContent);
});

logo.addEventListener('click', () => {
  input.value = '';
  results.replaceChildren();
  setSearchedLayout(false);
  updateSubmitState();
  input.focus();
});

function setSearchedLayout(searched) {
  app.classList.toggle('searched', searched);
  tagline.hidden = searched;
  examples.hidden = searched;
}

function setLoading(loading) {
  isSearching = loading;
  submit.classList.toggle('loading', loading);
  submitLabel.textContent = loading ? 'Searching' : 'Search';
  results.setAttribute('aria-busy', String(loading));
  updateSubmitState();
}

async function search(rawQuery) {
  const query = rawQuery.trim();
  if (query.length < 2 || isSearching) return;

  setPanelOpen(false);
  setHistoryOpen(false);
  setSearchedLayout(true);
  setLoading(true);
  renderSkeleton(Math.min(filters.count, 5));

  const near = nearInput.value.trim();

  try {
    const res = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        count: filters.count
      }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Search failed. Please try again in a moment.');

    renderResults(data.results || []);
    addHistory(query);
  } catch (err) {
    renderError(err instanceof Error ? err.message : 'Search failed.');
  } finally {
    setLoading(false);
  }
}

/* ---------- Rendering (textContent only, so results can't inject HTML) ---------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeHref(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null;
  } catch {
    return null;
  }
}

function renderSkeleton(count) {
  const wrapper = el('div');
  wrapper.append(el('span', 'sr-only', 'Finding the best places...'));

  for (let i = 0; i < count; i++) {
    const card = el('div', 'skeleton');
    const lines = el('div', 'skeleton-lines');
    lines.append(el('div'), el('div'), el('div'));
    card.append(el('div', 'skeleton-dot'), lines);
    wrapper.append(card);
  }

  results.replaceChildren(wrapper);
}

function renderError(message) {
  const p = el('p', 'error', message);
  p.setAttribute('role', 'alert');
  results.replaceChildren(p);
}

function renderResults(items) {
  if (items.length === 0) {
    results.replaceChildren(
      el('p', 'message', 'No places found. Try a different search or loosen your filters.')
    );
    return;
  }

  const list = el('ol', 'result-list');

  items.forEach((item, i) => {
    const li = el('li', 'result');
    li.style.animationDelay = `${i * 60}ms`;

    const body = el('div', 'result-body');
    const head = el('div', 'result-head');
    const badges = el('div', 'badges');

    const priceBadge = el('span', 'badge');
    priceBadge.append(
      el('span', 'sr-only', 'Price: '),
      document.createTextNode(PRICE_TEXT[item.price] || '')
    );

    badges.append(
      el('span', 'badge', item.kind === 'local' ? 'Local' : 'Online'),
      priceBadge
    );

    head.append(el('h3', null, item.name), badges);

    const why = el('p', 'why');
    why.append(
      el('strong', null, "Why it's a top pick: "),
      document.createTextNode(item.why)
    );

    body.append(head, el('p', 'summary', item.summary), why);

    if (item.address) body.append(el('p', 'address', item.address));

    const href = safeHref(item.url);
    if (href) {
      const link = el('a', 'result-link', `${new URL(href).hostname.replace(/^www\./, '')} ↗`);
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.append(el('span', 'sr-only', ' (opens in new tab)'));
      body.append(link);
    }

    li.append(el('span', 'rank', String(i + 1)), body);
    list.append(li);
  });

  results.replaceChildren(list);
}

updateSummary();
updateSubmitState();
renderHistory();
