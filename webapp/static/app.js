const form = document.getElementById('searchForm');
const resultsEl = document.getElementById('results');
const resultCountEl = document.getElementById('resultCount');
const resetBtn = document.getElementById('resetBtn');

const pdfModalEl = document.getElementById('pdfModal');
const pdfModal = new bootstrap.Modal(pdfModalEl);
const pdfFrame = document.getElementById('pdfFrame');
const pdfModalTitle = document.getElementById('pdfModalTitle');
const pdfModalSubtitle = document.getElementById('pdfModalSubtitle');
const downloadLink = document.getElementById('downloadLink');
const printBtn = document.getElementById('printBtn');
const PAGE_SIZE = 60;
let currentSkip = 0;
let currentTotalMatching = 0;

async function doSearch(reset = true) {
  if (reset) {
    currentSkip = 0;
    resultsEl.innerHTML = '';
  }

  const params = currentSearchParams();
  params.set('skip', currentSkip);
  params.set('limit', PAGE_SIZE);

  const res = await fetch('/api/search?' + params.toString());
  const data = await res.json();

  currentTotalMatching = data.total_matching;

  if (reset && data.results.length === 0) {
    resultsEl.innerHTML = '<div class="col-12 text-muted">Keine Ergebnisse gefunden.</div>';
  } else {
    for (const item of data.results) {
      resultsEl.appendChild(renderCard(item));
    }
  }

  currentSkip += data.results.length;

  const shown = resultsEl.querySelectorAll('.result-card').length;
  resultCountEl.textContent = `Zeige ${shown} von ${data.total_matching} Dokument(en)`;

  if (currentSkip < data.total_matching) {
    loadMoreBtn.classList.remove('d-none');
  } else {
    loadMoreBtn.classList.add('d-none');
  }
}

loadMoreBtn.addEventListener('click', () => {
  doSearch(false);
});

// --- Live-Status-Panel (pollt den Watcher-Fortschritt alle 4s) ---
function statusBadgeInfo(status) {
  switch (status) {
    case 'running': return { cls: 'bg-success blinking', label: 'Läuft' };
    case 'idle': return { cls: 'bg-secondary', label: 'Wartet' };
    case 'error': return { cls: 'bg-danger', label: 'Fehler' };
    default: return { cls: 'bg-secondary', label: 'Unbekannt' };
  }
}

async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const s = await res.json();

    const info = statusBadgeInfo(s.status);
    statusDot.className = 'status-dot ' + info.cls;

    let detailText = s.detail || (s.status === 'idle' ? 'Wartet auf nächsten Lauf' : '...');
    if (s.current_year && s.current_date_folder) {
      detailText += ` (${s.current_year}/${s.current_date_folder}${s.current_file ? ' – ' + s.current_file : ''})`;
    }
    statusDetail.textContent = detailText;

    let extraParts = [];
    if (s.status === 'idle' && s.last_run) extraParts.push(`Letzter Lauf: ${formatDateTime(s.last_run)}`);
    if (s.status === 'idle' && s.next_run) extraParts.push(`Nächster Lauf: ${formatDateTime(s.next_run)}`);
    if (typeof s.new_files_this_run === 'number' && s.new_files_this_run > 0) {
      extraParts.push(`Neue Dateien in diesem Lauf: ${s.new_files_this_run}`);
    }
    if (s.status === 'error' && s.last_error) extraParts.push(`Fehler: ${s.last_error}`);
    statusExtra.textContent = extraParts.join(' · ');

    if (s.phase === 'initial_scan' && s.total_folders) {
      statusProgressWrap.classList.remove('d-none');
      const pct = Math.min(100, Math.round((s.folders_done / s.total_folders) * 100));
      statusProgressBar.style.width = pct + '%';
      statusProgressBar.textContent = `${s.folders_done}/${s.total_folders}`;
    } else {
      statusProgressWrap.classList.add('d-none');
    }

    totalDocsBadge.textContent = `${s.total_documents.toLocaleString('de-DE')} Dokumente in der Datenbank`;
  } catch (e) {
    statusDetail.textContent = 'Status konnte nicht geladen werden';
    statusDot.className = 'status-dot bg-danger';
  }
}

doSearch(true);
pollStatus();
setInterval(pollStatus, 4000);
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function formatSize(bytes) {
  if (!bytes) return '';
  const kb = bytes / 1024;
  if (kb < 1024) return kb.toFixed(0) + ' KB';
  return (kb / 1024).toFixed(1) + ' MB';
}

function badgeClass(value, goodValues) {
  if (!value) return 'bg-secondary';
  return goodValues.includes(value.toLowerCase()) ? 'bg-success' : 'bg-danger';
}

function renderResults(items) {
  resultsEl.innerHTML = '';
  if (items.length === 0) {
    resultsEl.innerHTML = '<div class="col-12 text-muted">Keine Ergebnisse gefunden.</div>';
    return;
  }

  for (const item of items) {
    const col = document.createElement('div');
    col.className = 'col-sm-6 col-md-4 col-lg-3 col-xl-2';

    const thumbHtml = item.has_thumbnail
      ? `<img src="/thumbnail/${item.id}" alt="Vorschau">`
      : `<div class="thumb-placeholder">📄</div>`;

    const pruefBadge = item.pruefergebnis
      ? `<span class="badge ${badgeClass(item.pruefergebnis, ['stimmt überein'])}">${escapeHtml(item.pruefergebnis)}</span>`
      : '';
    const validierungBadge = item.stoffklassenvalidierung
      ? `<span class="badge ${badgeClass(item.stoffklassenvalidierung, ['valide'])}">${escapeHtml(item.stoffklassenvalidierung)}</span>`
      : '';

    col.innerHTML = `
      <div class="card result-card shadow-sm" data-id="${item.id}">
        <div class="thumb-wrapper">${thumbHtml}</div>
        <div class="card-body">
          <strong>${escapeHtml(item.measurement_datetime || item.date || '-')}</strong>
          <small class="text-muted">Gerät: ${escapeHtml(item.device_number || '?')} · Nr.: ${escapeHtml(item.measurement_seq || '?')}</small>
          <small class="text-muted">${escapeHtml(item.full_measurement_label || item.filename)}</small>
          <small class="text-muted">👤 ${escapeHtml(item.benutzer || '-')} · 🏢 ${escapeHtml(item.apotheke || '-')}</small>
          <div class="mt-1 d-flex gap-1 flex-wrap">${pruefBadge}${validierungBadge}</div>
          <small class="text-muted">${formatSize(item.file_size)}</small>
        </div>
      </div>
    `;
    col.querySelector('.result-card').addEventListener('click', () => openPdf(item));
    resultsEl.appendChild(col);
  }
}

function openPdf(item) {
  const url = `/pdf/${item.id}`;
  pdfFrame.src = url;
  pdfModalTitle.textContent = `${item.measurement_datetime || item.date || ''} – ${item.full_measurement_label || item.filename}`;
  pdfModalSubtitle.textContent = `Gerät: ${item.device_number || '?'} · Messung Nr.: ${item.measurement_seq || '?'} · Benutzer: ${item.benutzer || '-'} · Apotheke: ${item.apotheke || '-'}`;
  downloadLink.href = url;
  pdfModal.show();
}

printBtn.addEventListener('click', () => {
  try {
    pdfFrame.contentWindow.focus();
    pdfFrame.contentWindow.print();
  } catch (e) {
    window.open(pdfFrame.src, '_blank');
  }
});

pdfModalEl.addEventListener('hidden.bs.modal', () => {
  pdfFrame.src = '';
});

async function doSearch() {
  const params = new URLSearchParams(new FormData(form));
  for (const key of Array.from(params.keys())) {
    if (!params.get(key)) params.delete(key);
  }
  const res = await fetch('/api/search?' + params.toString());
  const data = await res.json();
  resultCountEl.textContent = `${data.count} Ergebnis(se)`;
  renderResults(data.results);
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  doSearch();
});

resetBtn.addEventListener('click', () => {
  form.reset();
  doSearch();
});

doSearch();