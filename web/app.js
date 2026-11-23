// QueryFlow Neo-Brutalist Studio Application Script
const examples = [
  'How many customers are there per country?',
  'Show customer names, order dates, and order status by joining customers and orders.',
  'Which products have never been ordered? Join products, order_items, and orders.',
  'What are the top 5 products by total revenue, joining products and order_items?',
  'Add a new customer named Rahul Sharma with email rahul@example.com from India and signup date 2026-08-31.',
  'Update the customer named Rahul Sharma to change the country to India.',
  'Delete the customer named Rahul Sharma with email rahul@example.com.'
];

const modePrompts = {
  read: 'Show customer names, order dates, and order status by joining customers and orders.',
  add: 'Add a new customer named Rahul Sharma with email rahul@example.com from India and signup date 2026-08-31.',
  update: 'Update the customer named Rahul Sharma to change the country to India.',
  delete: 'Delete the customer named Rahul Sharma with email rahul@example.com.'
};

const $ = id => document.getElementById(id);
const question = $('question'), runBtn = $('runBtn'), apiKey = $('apiKey');
const clearQuestionBtn = $('clearQuestionBtn');
let pendingToken = null;
let lastResultData = null;

// ==========================================
// Mode Selector (READ, ADD, UPDATE, DELETE)
// ==========================================
document.querySelectorAll('.mode-row').forEach(row => {
  row.onclick = () => {
    document.querySelectorAll('.mode-row').forEach(r => r.classList.remove('selected'));
    row.classList.add('selected');
    const mode = row.getAttribute('data-mode');
    if (modePrompts[mode]) {
      question.value = modePrompts[mode];
      question.focus();
      question.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };
});

// Quick CTA Buttons
const demoCtaBtn = $('demoCtaBtn');
if (demoCtaBtn) {
  demoCtaBtn.onclick = () => {
    question.value = 'Show customer names, order dates, and total spending by joining customers, orders and order_items.';
    question.focus();
    question.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
}

const newQueryBtn = $('newQueryBtn');
if (newQueryBtn) {
  newQueryBtn.onclick = () => {
    question.value = '';
    question.focus();
    question.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
}

if (clearQuestionBtn) {
  clearQuestionBtn.onclick = () => {
    question.value = '';
    question.focus();
  };
}

// Nav Links Smooth Scroll
const navHistoryLink = $('navHistoryLink');
if (navHistoryLink) {
  navHistoryLink.onclick = (e) => {
    e.preventDefault();
    const p = $('historyPanel');
    p.classList.remove('hidden');
    loadHistory();
    p.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
}

// ==========================================
// Example Prompt Chips
// ==========================================
examples.forEach(q => {
  const b = document.createElement('button');
  b.className = 'example';
  b.textContent = q;
  b.onclick = () => {
    question.value = q;
    question.focus();
    question.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  $('examples').appendChild(b);
});

// ==========================================
// Schema Explorer (Chips Format: "customers 40")
// ==========================================
async function loadSchema() {
  try {
    const r = await fetch('/api/schema');
    const d = await r.json();
    const tables = Object.keys(d);
    $('tableCount').textContent = `${tables.length}`;
    const list = $('schemaList');
    list.innerHTML = '';

    for (const [table, info] of Object.entries(d)) {
      const chip = document.createElement('div');
      chip.className = 'schema-chip';
      chip.setAttribute('title', `Click to query ${table}`);
      chip.innerHTML = `<b>${escapeHtml(table)}</b> <span>${info.row_count}</span>`;
      chip.onclick = () => {
        question.value = `Show 10 rows from ${table}.`;
        question.focus();
      };
      list.appendChild(chip);
    }
  } catch (e) {
    $('schemaList').innerHTML = '<div class="hint">Could not load schema.</div>';
  }
}

async function loadDb() {
  try {
    const r = await fetch('/api/database');
    const d = await r.json();
    $('dbName').textContent = d.name;
    $('dbStatus').textContent = `Using ${d.name}`;
    if (d.model && $('modelPill')) {
      const p = d.provider ? (d.provider.charAt(0).toUpperCase() + d.provider.slice(1)) : 'AI';
      $('modelPill').innerHTML = `<span></span> ${escapeHtml(p)} · ${escapeHtml(d.model)}`;
    }
  } catch (e) {}
}

$('customDbToggle').onchange = e => {
  $('uploadBox').classList.toggle('hidden', !e.target.checked);
};
$('uploadBtn').onclick = uploadSql;
$('resetDbBtn').onclick = resetDb;

async function uploadSql() {
  const f = $('sqlFile').files[0];
  if (!f) {
    showError('Select a .sql file first.');
    return;
  }
  setLoading(true);
  try {
    const fd = new FormData();
    fd.append('file', f);
    const r = await fetch('/api/upload-sql', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Import failed');
    await loadDb();
    await loadSchema();
    showSuccess(`Imported ${d.name}. Custom database is active.`);
  } catch (e) {
    showError(e.message);
  } finally {
    setLoading(false);
  }
}

async function resetDb() {
  try {
    const r = await fetch('/api/reset-database', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Reset failed');
    $('customDbToggle').checked = false;
    $('uploadBox').classList.add('hidden');
    await loadDb();
    await loadSchema();
    showSuccess('Switched back to sample.db.');
  } catch (e) {
    showError(e.message);
  }
}

// ==========================================
// Query & Write Execution
// ==========================================
runBtn.onclick = runQuery;
question.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') runQuery();
});

$('confirmCheckbox').addEventListener('change', e => {
  $('confirmBtn').disabled = !e.target.checked;
});
$('confirmBtn').onclick = executePending;

async function runQuery() {
  const q = question.value.trim();
  if (!q) {
    showError('Please type a question first.');
    question.focus();
    return;
  }
  setLoading(true);
  hide('error');
  hide('result');
  hide('confirmation');
  hide('emptyState');
  pendingToken = null;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 55000);

  try {
    const r = await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
      body: JSON.stringify({
        question: q,
        api_key: apiKey.value.trim() || null
      })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Request failed');
    if (!d.ok) {
      if (d.sql) renderSqlOnly(d.sql);
      throw new Error(d.error || 'SQL was blocked.');
    }

    if (d.requires_confirmation) {
      pendingToken = d.token;
      $('confirmBadge').textContent = d.operation === 'insert' ? 'ADD DATA' : d.operation === 'update' ? 'UPDATE DATA' : 'DELETE DATA';
      $('confirmBadge').className = 'confirm-badge ' + (d.operation !== 'insert' ? 'danger' : '');
      $('confirmTitle').textContent = d.operation === 'insert' ? 'Confirm Data Addition' : d.operation === 'update' ? 'Confirm Data Update' : 'Confirm Data Deletion';
      $('confirmMessage').textContent = d.message;
      $('pendingSql').querySelector('code').textContent = d.sql;
      $('confirmLabel').textContent = d.operation === 'insert'
        ? 'I have reviewed this SQL and understand that it will add data to the database.'
        : d.operation === 'update'
        ? 'I have reviewed this SQL and understand that it will modify matching data in the database.'
        : 'I have reviewed this SQL and understand that it will permanently delete matching data.';
      $('confirmCheckbox').checked = false;
      $('confirmBtn').disabled = true;
      $('confirmBtn').textContent = d.operation === 'insert' ? 'Confirm & Add' : d.operation === 'update' ? 'Confirm & Update' : 'Confirm & Delete';
      $('confirmation').classList.remove('hidden');
      $('confirmation').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      loadHistory();
      return;
    }

    renderResults(d);
    loadHistory();
  } catch (e) {
    showError(e.name === 'AbortError' ? 'The AI provider took too long to respond. Check your API key and provider status, then try again.' : e.message);
  } finally {
    clearTimeout(timeoutId);
    setLoading(false);
  }
}

async function executePending() {
  if (!pendingToken || !$('confirmCheckbox').checked) return;
  $('confirmBtn').disabled = true;
  $('confirmBtn').textContent = 'Executing…';
  try {
    const r = await fetch('/api/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: pendingToken, confirmed: true })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Execution failed');

    hide('confirmation');
    $('emptyState').classList.remove('hidden');
    $('emptyState').querySelector('h2').textContent = d.operation === 'insert' ? '✓ Data Added' : d.operation === 'update' ? '✓ Data Updated' : '✓ Data Deleted';
    $('emptyState').querySelector('p').textContent = `${d.affected_rows} row${d.affected_rows === 1 ? '' : 's'} affected. Ask another question to inspect the database.`;
    await loadSchema();
    loadHistory();
  } catch (e) {
    showError(e.message);
  } finally {
    pendingToken = null;
    $('confirmBtn').textContent = 'Confirm & Execute';
  }
}

function renderResults(d) {
  lastResultData = d;
  $('sql').querySelector('code').textContent = d.sql;
  $('rowCount').textContent = `${d.row_count} row${d.row_count === 1 ? '' : 's'}`;
  const thead = $('resultTable').querySelector('thead');
  const tbody = $('resultTable').querySelector('tbody');
  thead.innerHTML = '';
  tbody.innerHTML = '';

  const hr = document.createElement('tr');
  const thIdx = document.createElement('th');
  thIdx.textContent = '#';
  thIdx.style.width = '36px';
  hr.appendChild(thIdx);

  d.columns.forEach(c => {
    const th = document.createElement('th');
    th.textContent = c;
    hr.appendChild(th);
  });
  thead.appendChild(hr);

  d.rows.forEach((row, i) => {
    const tr = document.createElement('tr');
    const tdIdx = document.createElement('td');
    tdIdx.textContent = i + 1;
    tr.appendChild(tdIdx);

    d.columns.forEach(c => {
      const td = document.createElement('td');
      td.textContent = row[c] === null ? 'NULL' : row[c];
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  $('result').classList.remove('hidden');
  $('result').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderSqlOnly(sql) {
  lastResultData = null;
  $('sql').querySelector('code').textContent = sql;
  $('result').classList.remove('hidden');
  $('rowCount').textContent = '';
  $('resultTable').querySelector('thead').innerHTML = '';
  $('resultTable').querySelector('tbody').innerHTML = '';
}

function setLoading(v) {
  runBtn.disabled = v;
  runBtn.innerHTML = v ? '<span>Synthesizing…</span>' : '<span>Generate SQL</span> <span class="btn-icon">→</span>';
  $('loading').classList.toggle('hidden', !v);
}

function showError(msg) {
  $('error').textContent = msg;
  $('error').classList.remove('hidden');
  $('emptyState').classList.add('hidden');
}

function showSuccess(msg) {
  hide('error');
  $('emptyState').classList.remove('hidden');
  $('emptyState').querySelector('h2').textContent = '✓ ' + msg;
  $('emptyState').querySelector('p').textContent = 'Database refreshed.';
}

function hide(id) {
  const el = $(id);
  if (el) el.classList.add('hidden');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>'"]/g, c => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  }[c]));
}

// Copy SQL Button
$('copyBtn').onclick = async () => {
  const code = $('sql').querySelector('code').innerText;
  await navigator.clipboard.writeText(code);
  $('copyBtn').textContent = 'Copied ✓';
  setTimeout(() => $('copyBtn').textContent = 'Copy SQL', 1400);
};

// Export CSV Button
const exportCsvBtn = $('exportCsvBtn');
if (exportCsvBtn) {
  exportCsvBtn.onclick = () => {
    if (!lastResultData || !lastResultData.columns || !lastResultData.rows) return;
    const cols = lastResultData.columns;
    const rows = lastResultData.rows;
    const csvLines = [];
    csvLines.push(cols.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','));
    rows.forEach(r => {
      csvLines.push(cols.map(c => {
        const v = r[c];
        return v === null ? '' : `"${String(v).replace(/"/g, '""')}"`;
      }).join(','));
    });
    const blob = new Blob([csvLines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `queryflow_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };
}

// History Panel
$('historyBtn').onclick = () => {
  const p = $('historyPanel');
  p.classList.toggle('hidden');
  if (!p.classList.contains('hidden')) {
    loadHistory();
    p.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
};

$('clearHistoryBtn').onclick = async () => {
  await fetch('/api/history', { method: 'DELETE' });
  loadHistory();
};

async function loadHistory() {
  try {
    const r = await fetch('/api/history');
    const rows = await r.json();
    const list = $('historyList');
    list.innerHTML = '';
    if (!rows.length) {
      list.innerHTML = '<div class="hint">No SQL history yet.</div>';
      return;
    }
    rows.forEach(h => {
      const el = document.createElement('div');
      el.className = 'history-item';
      el.innerHTML = `
        <div class="history-top">
          <span class="history-op ${h.operation}">${h.operation.toUpperCase()}</span>
          <span>${escapeHtml(h.created_at)} · ${escapeHtml(h.database_name)}</span>
        </div>
        <button class="history-question">${escapeHtml(h.question)}</button>
        <pre><code>${escapeHtml(h.sql)}</code></pre>
        <div class="history-status">${escapeHtml(h.status)}${h.affected_rows !== null ? ' · ' + h.affected_rows + ' rows' : ''}</div>
      `;
      el.querySelector('.history-question').onclick = () => {
        question.value = h.question;
        question.focus();
        question.scrollIntoView({ behavior: 'smooth', block: 'center' });
      };
      list.appendChild(el);
    });
  } catch (e) {}
}

// Initial Load
loadDb();
loadSchema();

// Multimodal media library
document.querySelector('a[href="#mediaSection"]')?.addEventListener('click', () => {
  setTimeout(loadMedia, 0);
});
document.querySelector('a[href="#mediaSearchSection"]')?.addEventListener('click', () => {
  setTimeout(loadMedia, 0);
});

function mediaCard(item) {
  const card = document.createElement('article');
  card.className = 'media-card';
  const title = document.createElement('strong');
  title.textContent = item.original_name;
  const meta = document.createElement('div');
  meta.className = 'hint';
  const size = Number(item.file_size || 0);
  meta.textContent = `${item.media_type} · ${(size / 1048576).toFixed(2)} MB · ${item.processing_status || 'completed'}`;
  card.append(title, meta);
  if (item.width && item.height) {
    const dimensions = document.createElement('div');
    dimensions.className = 'hint';
    dimensions.textContent = `${item.width} × ${item.height}${item.duration_seconds ? ` · ${Number(item.duration_seconds).toFixed(1)} sec` : ''}`;
    card.append(dimensions);
  }
  const contentText = item.description || item.ai_summary || item.extracted_text;
  if (contentText) {
    const content = document.createElement('p');
    content.className = 'media-copy';
    content.textContent = contentText;
    card.append(content);
  }
  if (item.processing_error) {
    const error = document.createElement('p');
    error.className = 'media-error';
    error.textContent = item.processing_error;
    card.append(error);
  }
  const fileAvailable = item.file_available !== false;
  if (!fileAvailable) {
    const unavailable = document.createElement('p');
    unavailable.className = 'hint';
    unavailable.textContent = 'Metadata found in the database; the original file is not stored in QueryFlow’s media folder.';
    card.append(unavailable);
  }
  if (fileAvailable && ['image', 'audio', 'video'].includes(item.media_type)) {
    const preview = document.createElement(item.media_type === 'image' ? 'img' : item.media_type);
    preview.src = `/api/media/${encodeURIComponent(item.media_id)}/content`;
    if (item.media_type !== 'image') preview.controls = true;
    preview.className = 'media-preview';
    if (item.media_type === 'image') preview.alt = item.original_name;
    card.append(preview);
  }
  const actions = document.createElement('div');
  actions.className = 'media-actions';
  if (fileAvailable) {
  const download = document.createElement('a');
  download.href = `/api/media/${encodeURIComponent(item.media_id)}/download`;
  download.textContent = 'Download';
  const retry = document.createElement('button');
  retry.type = 'button'; retry.className = 'text-btn'; retry.textContent = 'Reprocess';
  retry.onclick = async () => {
    const response = await fetch(`/api/media/${encodeURIComponent(item.media_id)}/process`, { method: 'POST' });
    if (!response.ok) { $('mediaMessage').textContent = 'Could not queue processing.'; return; }
    $('mediaMessage').textContent = `Queued ${item.original_name} for processing.`;
    loadMedia();
  };
  const remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'text-btn'; remove.textContent = 'Delete';
  remove.onclick = async () => {
    if (!confirm(`Delete ${item.original_name}?`)) return;
    const response = await fetch(`/api/media/${encodeURIComponent(item.media_id)}`, { method: 'DELETE' });
    if (!response.ok) { $('mediaMessage').textContent = 'Delete failed.'; return; }
    loadMedia();
  };
  actions.append(download, retry, remove);
  card.append(actions);
  }
  return card;
}

async function loadMedia() {
  const grid = $('mediaGrid');
  if (!grid) return;
  grid.innerHTML = '<p class="hint">Loading media…</p>';
  try {
    const type = $('mediaTypeFilter').value;
    const response = await fetch('/api/media' + (type ? `?media_type=${encodeURIComponent(type)}` : ''));
    const data = await parseMediaResponse(response);
    grid.replaceChildren();
    if (!data.files.length) { grid.innerHTML = '<p class="hint">No files yet. Upload files to get started.</p>'; return; }
    data.files.forEach(item => grid.append(mediaCard(item)));
  } catch (error) { grid.textContent = error.message; }
}

async function parseMediaResponse(response) {
  const body = await response.text();
  let data;
  try { data = JSON.parse(body); }
  catch { throw new Error(`Media API returned HTTP ${response.status}: ${body.slice(0, 180) || 'empty response'}`); }
  if (!response.ok) throw new Error(data.detail || `Media request failed (HTTP ${response.status}).`);
  return data;
}

async function uploadMedia() {
  const files = $('mediaFiles').files;
  const message = $('mediaMessage');
  if (!files.length) { message.textContent = 'Choose at least one file.'; return; }
  if (files.length > 20) { message.textContent = 'Choose no more than 20 files at once.'; return; }
  const form = new FormData();
  for (const file of files) form.append('files', file);
  $('mediaUploadBtn').disabled = true;
  message.textContent = 'Uploading…';
  try {
    const response = await fetch('/api/media/upload', { method: 'POST', body: form });
    const data = await parseMediaResponse(response);
    message.textContent = `Uploaded ${data.files.length} file(s). Processing runs in the background.`;
    $('mediaFiles').value = '';
    await loadMedia();
  } catch (error) { message.textContent = error.message; }
  finally { $('mediaUploadBtn').disabled = false; }
}

$('mediaUploadBtn')?.addEventListener('click', uploadMedia);
$('refreshMedia')?.addEventListener('click', loadMedia);
$('mediaTypeFilter')?.addEventListener('change', loadMedia);
$('mediaSearchBtn')?.addEventListener('click', async () => {
  const box = $('mediaSearchResults');
  const query = $('mediaSearchQuery').value.trim();
  if (!query) { box.textContent = 'Enter a few search words.'; return; }
  box.innerHTML = '<p class="hint">Searching…</p>';
  try {
    const response = await fetch('/api/media/semantic-search', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query })
    });
    const data = await parseMediaResponse(response);
    box.replaceChildren();
    if (!data.results.length) { box.innerHTML = '<p class="hint">No matching files found.</p>'; return; }
    data.results.forEach(item => box.append(mediaCard(item)));
  } catch (error) { box.textContent = error.message; }
});
