function $(id) {
  return document.getElementById(id);
}

function showToast(message, isError = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("hidden", false);
  toast.style.borderColor = isError ? "var(--error)" : "var(--border)";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add("hidden"), 4000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body;
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function formatBytes(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderResults(data) {
  $("results-section").classList.remove("hidden");

  const company = data.company_name ? ` — ${data.company_name}` : "";
  $("results-title").textContent = data.ticker;
  $("results-subtitle").textContent = company ? data.company_name : "No company name in metadata";

  $("browse-summary").innerHTML = `
    <span class="coverage-pill">Mongo docs: <strong>${data.mongo.count}</strong></span>
    <span class="coverage-pill">Accession dirs: <strong>${data.filesystem.accession_count}</strong></span>
    <span class="coverage-pill">Files on disk: <strong>${data.filesystem.file_count}</strong></span>
    <span class="coverage-pill">Dir exists: <strong>${data.filesystem.exists ? "yes" : "no"}</strong></span>
  `;

  $("mongo-collection").textContent = data.mongo.collection;
  const mongoBody = $("mongo-table-body");
  const mongoEmpty = $("mongo-empty");

  if (data.mongo.count === 0) {
    mongoBody.innerHTML = "";
    mongoEmpty.classList.remove("hidden");
  } else {
    mongoEmpty.classList.add("hidden");
    mongoBody.innerHTML = data.mongo.filings
      .map(
        (f) => `
        <tr>
          <td><strong>${f.form}</strong></td>
          <td>${f.filing_date}</td>
          <td><code>${f.accession_number}</code></td>
          <td>${formatDate(f.downloaded_at)}</td>
          <td class="path-cell"><code>${f.local_path}</code></td>
        </tr>
      `
      )
      .join("");
  }

  $("fs-path").textContent = data.filesystem.ticker_path;
  const fsBody = $("fs-table-body");
  const fsEmpty = $("fs-empty");

  if (data.filesystem.file_count === 0) {
    fsBody.innerHTML = "";
    fsEmpty.classList.remove("hidden");
    fsEmpty.textContent = data.filesystem.exists
      ? "Ticker directory exists but contains no files."
      : "Ticker directory does not exist on disk.";
  } else {
    fsEmpty.classList.add("hidden");
    fsBody.innerHTML = data.filesystem.entries
      .map(
        (entry) => `
        <tr>
          <td><code>${entry.accession_dir}</code></td>
          <td><code>${entry.name}</code></td>
          <td>${formatBytes(entry.size_bytes)}</td>
          <td>${formatDate(entry.modified_at)}</td>
        </tr>
      `
      )
      .join("");
  }
}

async function browseTicker(ticker) {
  const normalized = ticker.trim().toUpperCase();
  if (!normalized) return;

  const button = $("browse-form").querySelector("button");
  button.disabled = true;

  try {
    const data = await api(`/api/browse/${encodeURIComponent(normalized)}`);
    renderResults(data);
    history.replaceState(null, "", `?ticker=${encodeURIComponent(normalized)}`);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

function wireForm() {
  $("browse-form").addEventListener("submit", (event) => {
    event.preventDefault();
    browseTicker($("browse-ticker").value);
  });
}

function initFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const ticker = params.get("ticker");
  if (ticker) {
    $("browse-ticker").value = ticker;
    browseTicker(ticker);
  }
}

wireForm();
initFromQuery();
