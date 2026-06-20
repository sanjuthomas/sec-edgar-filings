const LOOKBACK_OPTIONS = [
  { label: "1 day", days: 1 },
  { label: "1 week", days: 7 },
  { label: "1 month", days: 30 },
  { label: "1 year", days: 365 },
  { label: "2 years", days: 730 },
  { label: "3 years", days: 1095 },
  { label: "4 years", days: 1460 },
  { label: "5 years", days: 1825 },
  { label: "10 years", days: 3650 },
];

const POLL_MS = 2500;

let pollTimer = null;
let activeJobId = null;

function $(id) {
  return document.getElementById(id);
}

function populateLookbackSelects() {
  for (const select of document.querySelectorAll(".lookback-select")) {
    select.innerHTML = LOOKBACK_OPTIONS.map(
      (opt) => `<option value="${opt.days}">${opt.label}</option>`
    ).join("");
    select.value = "365";
  }
}

function showToast(message, isError = false, durationMs = 4000) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("hidden", false);
  toast.style.borderColor = isError ? "var(--error)" : "var(--border)";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toast.classList.add("hidden"), durationMs);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const hasBody = options.body != null;
  if (hasBody && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function updateJobUI(job) {
  const badge = $("job-status-badge");
  const noJob = $("no-job");
  const details = $("job-details");

  if (!job || (job.status !== "pending" && job.status !== "running" && !activeJobId)) {
    if (!job) {
      badge.textContent = "idle";
      badge.className = "badge";
      noJob.classList.remove("hidden");
      details.classList.add("hidden");
      return;
    }
  }

  noJob.classList.add("hidden");
  details.classList.remove("hidden");

  badge.textContent = job.status;
  badge.className = `badge ${job.status}`;

  const completed = job.tickers_completed || 0;
  const total = job.tickers_total || 0;
  const failed = job.tickers_failed || 0;
  const remaining = Math.max(total - completed, 0);

  $("current-ticker").textContent = job.current_ticker || job.ticker || "—";
  $("completed-count").textContent = String(completed);
  $("remaining-count").textContent = String(remaining);
  $("failed-count").textContent = String(failed);

  const pct = total > 0 ? Math.min((completed / total) * 100, 100) : 0;
  $("progress-bar").style.width = `${pct}%`;

  $("job-message").textContent = job.message || "";
  const errorEl = $("job-error");
  if (job.error) {
    errorEl.textContent = job.error;
    errorEl.classList.remove("hidden");
  } else {
    errorEl.classList.add("hidden");
  }
}

async function refreshJob() {
  try {
    const job = await api("/api/jobs/current");
    if (job) {
      activeJobId = job.job_id;
      updateJobUI(job);
      if (job.status === "completed" || job.status === "failed") {
        stopPolling();
        await refreshUniverse();
      }
      return;
    }

    if (activeJobId) {
      const lastJob = await api(`/api/jobs/${activeJobId}`);
      updateJobUI(lastJob);
      if (lastJob.status === "completed" || lastJob.status === "failed") {
        stopPolling();
        await refreshUniverse();
      }
      return;
    }

    updateJobUI(null);
  } catch (err) {
    console.error(err);
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refreshJob, POLL_MS);
  refreshJob();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refreshRuntimeMeta() {
  try {
    const [config, stats] = await Promise.all([
      api("/api/config"),
      api("/api/stats"),
    ]);
    $("runtime-meta").innerHTML = `
      <div>Kafka: <strong>${config.kafka_enabled ? "enabled" : "disabled"}</strong></div>
      <div>Topic: <code>${config.kafka_filing_downloaded_topic}</code></div>
      <div>Stored filings: <strong>${stats.filing_metadata_count}</strong></div>
    `;
  } catch (err) {
    $("runtime-meta").textContent = "Unable to load runtime config";
  }
}

async function refreshUniverse() {
  try {
    const summary = await api("/api/universe/sp500/status");
    $("coverage-stats").innerHTML = `
      <span class="coverage-pill">Active: <strong>${summary.active_count}</strong></span>
      <span class="coverage-pill">OK: <strong>${summary.downloaded_ok}</strong></span>
      <span class="coverage-pill">Errors: <strong>${summary.downloaded_error}</strong></span>
      <span class="coverage-pill">Never run: <strong>${summary.never_downloaded}</strong></span>
    `;

    const tbody = $("ticker-table-body");
    tbody.innerHTML = summary.tickers
      .map((t) => {
        const statusClass =
          t.last_download_status === "ok"
            ? "status-ok"
            : t.last_download_status === "error"
              ? "status-error"
              : "status-pending";
        const statusLabel = t.last_download_status || "pending";
        return `
          <tr>
            <td><strong>${t.ticker}</strong></td>
            <td>${t.company_name || "—"}</td>
            <td class="${statusClass}">${statusLabel}</td>
            <td>${formatDate(t.last_download_at)}</td>
            <td>${t.last_download_filings_found ?? "—"}</td>
            <td>${t.last_download_filings_downloaded ?? "—"}</td>
            <td>${t.last_download_filings_skipped ?? "—"}</td>
          </tr>
        `;
      })
      .join("");
  } catch (err) {
    showToast(err.message, true);
  }
}

async function startJob(path, body) {
  try {
    const job = await api(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    activeJobId = job.job_id;
    updateJobUI(job);
    startPolling();
    showToast(`Job ${job.job_id} started`);
  } catch (err) {
    if (err.status === 409 && err.body?.detail?.current_job) {
      activeJobId = err.body.detail.current_job.job_id;
      updateJobUI(err.body.detail.current_job);
      startPolling();
    }
    showToast(err.message, true);
  }
}

function wireForms() {
  $("single-ticker-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const ticker = $("single-ticker").value.trim().toUpperCase();
    const lookback_days = Number($("single-lookback").value);
    startJob("/api/jobs/download/ticker", { ticker, lookback_days });
  });

  $("batch-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const lookback_days = Number($("batch-lookback").value);
    const skip_refresh = $("batch-skip-refresh").checked;
    startJob("/api/jobs/download/batch", { lookback_days, skip_refresh });
  });

  $("reload-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!$("reload-confirm").checked) {
      showToast("Confirm metadata clear before full reload", true);
      return;
    }
    const lookback_days = Number($("reload-lookback").value);
    const skip_refresh = $("reload-skip-refresh").checked;
    startJob("/api/jobs/download/full-reload", { lookback_days, skip_refresh });
  });

  $("delete-filings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!$("delete-confirm").checked) {
      showToast("Check the confirmation box to delete filings", true);
      return;
    }
    const ticker = $("delete-ticker").value.trim().toUpperCase();
    if (!ticker) {
      showToast("Enter a ticker to delete", true);
      return;
    }

    const button = event.submitter || $("delete-filings-form").querySelector("button");
    button.disabled = true;
    try {
      const result = await api(`/api/filings/${encodeURIComponent(ticker)}`, {
        method: "DELETE",
      });
      $("delete-confirm").checked = false;
      $("delete-ticker").value = "";
      await refreshRuntimeMeta();
      await refreshUniverse();
      showToast(
        `Deleted ${result.deleted_count} MongoDB doc(s) and ${result.files_deleted} file(s) for ${result.ticker}`,
        false,
        8000
      );
    } catch (err) {
      showToast(err.message, true);
    } finally {
      button.disabled = false;
    }
  });

  $("refresh-universe").addEventListener("click", refreshUniverse);
}

async function init() {
  populateLookbackSelects();
  wireForms();
  await refreshRuntimeMeta();
  await refreshUniverse();
  await refreshJob();
  if (activeJobId) {
    startPolling();
  }
}

init();
