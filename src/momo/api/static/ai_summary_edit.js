(() => {
  const DEBOUNCE_MS = 300;
  const modal = document.getElementById("ai-summary-modal");
  if (!modal) return;

  const titleEl = document.getElementById("ai-summary-modal-title");
  const symbolEl = document.getElementById("ai-summary-modal-symbol");
  const textarea = document.getElementById("ai-summary-modal-text");
  const preview = document.getElementById("ai-summary-modal-preview");
  const statusEl = document.getElementById("ai-summary-modal-status");
  const saveBtn = document.getElementById("ai-summary-modal-save");
  const closeBtns = modal.querySelectorAll("[data-ai-summary-close]");

  let currentSymbol = "";
  let currentName = "";
  let debounceTimer = null;
  let previewSeq = 0;
  let lastPreviewText = null;

  function setStatus(msg, isError = false) {
    if (!statusEl) return;
    statusEl.textContent = msg || "";
    statusEl.classList.toggle("is-error", Boolean(isError && msg));
  }

  function openModal({ symbol, name, summary }) {
    currentSymbol = symbol;
    currentName = name || "";
    lastPreviewText = null;
    titleEl.textContent = name ? `Edit AI summary — ${name}` : "Edit AI summary";
    symbolEl.textContent = symbol;
    textarea.value = summary || "";
    preview.innerHTML = summary
      ? '<p class="meta">Formatting preview…</p>'
      : '<p class="meta">Paste a summary to preview.</p>';
    setStatus("");
    modal.hidden = false;
    document.body.classList.add("modal-open");
    textarea.focus();
    // Run immediately, then keep debounced updates on further edits.
    refreshPreview();
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    currentSymbol = "";
    currentName = "";
    lastPreviewText = null;
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
  }

  async function refreshPreview() {
    const seq = ++previewSeq;
    const text = textarea.value;
    if (!text.trim()) {
      lastPreviewText = "";
      preview.innerHTML = '<p class="meta">Paste a summary to preview.</p>';
      setStatus("");
      return;
    }
    // Skip duplicate in-flight work for identical text, but always render once.
    if (text === lastPreviewText && preview.dataset.ready === "1") {
      return;
    }
    setStatus("Updating preview…");
    try {
      const res = await fetch("/api/ai-summary/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(() => ({}));
      if (seq !== previewSeq) return;
      if (!res.ok) {
        throw new Error(data.error || data.detail || `Preview failed (${res.status})`);
      }
      lastPreviewText = text;
      preview.dataset.ready = "1";
      preview.innerHTML = data.html || '<p class="meta">Nothing to preview.</p>';
      setStatus("Preview updated");
    } catch (err) {
      if (seq !== previewSeq) return;
      preview.dataset.ready = "0";
      preview.innerHTML = `<p class="error">${err.message || "Preview failed"}</p>`;
      setStatus(err.message || "Preview failed", true);
    }
  }

  function schedulePreview() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      refreshPreview();
    }, DEBOUNCE_MS);
  }

  async function saveSummary() {
    if (!currentSymbol) return;
    const text = textarea.value;
    if (!text.trim()) {
      setStatus("Summary text is empty.", true);
      return;
    }
    saveBtn.disabled = true;
    setStatus("Saving…");
    try {
      const res = await fetch(`/api/ai-summary/${encodeURIComponent(currentSymbol)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ text, stock_name: currentName || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || `Save failed (${res.status})`);
      }
      setStatus("Saved.");
      window.location.reload();
    } catch (err) {
      setStatus(err.message || "Save failed", true);
      saveBtn.disabled = false;
    }
  }

  document.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-ai-summary-edit]");
    if (!btn) return;
    event.preventDefault();
    const symbol = btn.getAttribute("data-symbol");
    const name = btn.getAttribute("data-name") || symbol;
    if (!symbol) return;
    setStatus("");
    try {
      const res = await fetch(`/api/ai-summary/${encodeURIComponent(symbol)}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`Failed to load summary (${res.status})`);
      const data = await res.json();
      openModal({
        symbol: data.symbol || symbol,
        name: data.stock_name || name,
        summary: data.summary || "",
      });
    } catch (err) {
      openModal({ symbol, name, summary: "" });
      setStatus(err.message || "Could not load existing summary.", true);
    }
  });

  textarea.addEventListener("input", schedulePreview);
  textarea.addEventListener("paste", () => {
    // Paste updates value after the event; schedule on next tick + debounce.
    setTimeout(schedulePreview, 0);
  });
  saveBtn.addEventListener("click", (event) => {
    event.preventDefault();
    saveSummary();
  });
  closeBtns.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      closeModal();
    });
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
})();
