const form = document.getElementById('searchForm');
const resultsEl = document.getElementById('results');
const resultCountEl = document.getElementById('resultCount');
const resetBtn = document.getElementById('resetBtn');

const pdfModalEl = document.getElementById('pdfModal');
const pdfModal = new bootstrap.Modal(pdfModalEl);
const pdfFrame = document.getElementById('pdfFrame');
const pdfModalTitle = document.getElementById('pdfModalTitle');
const downloadLink = document.getElementById('downloadLink');
const printBtn = document.getElementById('printBtn');

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

    col.innerHTML = `
      <div class="card result-card shadow-sm" data-id="${item.id}">
        <div class="thumb-wrapper">${thumbHtml}</div>
        <div class="card-body">
          <strong>${escapeHtml(item.date || '-')}</strong>
          <small class="text-muted">Gerät: ${escapeHtml(item.device_number || '?')}</small>
          <small class="text-muted">Messung Nr.: ${escapeHtml(item.measurement_seq || '?')}</small>
          <small class="text-muted">${escapeHtml(item.full_measurement_label || item.filename)}</small>
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
  pdfModalTitle.textContent = `${item.date || ''} – ${item.full_measurement_label || item.filename}`;
  downloadLink.href = url;
  pdfModal.show();
}

printBtn.addEventListener('click', () => {
  // Nutzt den nativen PDF-Viewer im iframe zum Drucken.
  try {
    pdfFrame.contentWindow.focus();
    pdfFrame.contentWindow.print();
  } catch (e) {
    // Fallback: PDF in neuem Tab öffnen, dort kann über den Browser-eigenen
    // PDF-Viewer (Drucker-Symbol) gedruckt werden.
    window.open(pdfFrame.src, '_blank');
  }
});

// Beim Schliessen des Modals: iframe leeren, damit kein Ton/Player weiterlaeuft
pdfModalEl.addEventListener('hidden.bs.modal', () => {
  pdfFrame.src = '';
});

async function doSearch() {
  const params = new URLSearchParams(new FormData(form));
  // leere Felder nicht mitschicken
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

// Initiale Suche beim Laden der Seite (letzte Ergebnisse anzeigen)
doSearch();