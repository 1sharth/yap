/**
 * Main Web UI Controller for Yap
 */

document.addEventListener("DOMContentLoaded", () => {
  // Elements
  const sessionsListEl = document.getElementById("sessions-list");
  const sessionHeaderEl = document.getElementById("session-header");
  const sessionTitleEl = document.getElementById("session-title");
  const sessionDateEl = document.getElementById("session-date");
  const sessionStatsEl = document.getElementById("session-stats");
  const sessionClientBadgeEl = document.getElementById("session-client-badge");
  const turnsContainerEl = document.getElementById("turns-container");
  const emptyStateEl = document.getElementById("empty-state");

  const btnNewSession = document.getElementById("btn-new-session");
  const sessionSearchInput = document.getElementById("session-search");
  const btnCopyRaw = document.getElementById("btn-copy-raw");
  const btnExportMd = document.getElementById("btn-export-md");
  const btnDeleteSession = document.getElementById("btn-delete-session");

  const deleteDialog = document.getElementById("delete-dialog");
  const btnConfirmDelete = document.getElementById("btn-confirm-delete");
  const deleteAudioCheckbox = document.getElementById("delete-audio-checkbox");

  const btnRecord = document.getElementById("btn-record");
  const recordStatusEl = document.getElementById("record-status");
  const recordTimerEl = document.getElementById("record-timer");
  const visualizerCanvas = document.getElementById("audio-visualizer");
  const toggleAutocopy = document.getElementById("toggle-autocopy");
  const engineDot = document.getElementById("engine-dot");
  const engineStatusText = document.getElementById("engine-status-text");

  // State
  let currentSessionId = null;
  let currentSession = null;
  let isRecording = false;
  let deleteTargetSessionId = null;

  // Initialize auto-copy preference
  const savedAutoCopy = localStorage.getItem("yap_autocopy");
  if (savedAutoCopy !== null) {
    toggleAutocopy.checked = savedAutoCopy === "true";
  }
  toggleAutocopy.addEventListener("change", () => {
    localStorage.setItem("yap_autocopy", toggleAutocopy.checked);
  });

  // Toast helper
  function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(8px)";
      setTimeout(() => toast.remove(), 250);
    }, 3000);
  }

  // Voice recorder instance
  const recorder = new VoiceRecorder(
    visualizerCanvas,
    (timeStr) => {
      recordTimerEl.textContent = timeStr;
    },
    async (blob) => {
      recordStatusEl.textContent = "Transcribing...";
      btnRecord.disabled = true;
      btnRecord.classList.remove("recording");
      isRecording = false;

      try {
        if (!currentSessionId) {
          const newSess = await API.createSession("New Voice Session", "web", true);
          currentSessionId = newSess.id;
        }

        const res = await API.uploadAudio(currentSessionId, blob);
        recordStatusEl.textContent = "Click to speak";
        recordTimerEl.textContent = "00:00";

        if (res.turn) {
          // If auto-copy enabled, copy to clipboard
          if (toggleAutocopy.checked && res.text) {
            navigator.clipboard.writeText(res.text).catch(() => {});
            showToast(`Copied (${res.infer_duration}s): "${res.text.slice(0, 36)}..."`);
          } else {
            showToast(`Transcribed in ${res.infer_duration}s`);
          }

          // Reload session details to display updated turns
          btnRecord.disabled = false;
          await selectSession(currentSessionId, false);
          await loadSessionsList();
        } else {
          showToast("No speech detected", "danger");
        }
      } catch (err) {
        recordStatusEl.textContent = "Click to speak";
        recordTimerEl.textContent = "00:00";
        showToast(`Transcription error: ${err.message}`, "danger");
      } finally {
        btnRecord.disabled = false;
      }
    }
  );

  // Health check
  async function checkServerHealth() {
    const health = await API.checkHealth();
    document.getElementById("footer-engine-dot").className = health.status === "ready" ? "status-dot ready" : "status-dot";
    if (health.status === "ready") {
      engineDot.className = "status-dot ready";
      engineStatusText.textContent = `Whisper ready (${health.device})`;
    } else {
      engineDot.className = "status-dot";
      engineStatusText.textContent = "Daemon disconnected";
    }
  }

  // Load sessions list in sidebar
  async function loadSessionsList(search = "") {
    try {
      const sessions = await API.fetchSessions(search);
      renderSessionsList(sessions);
    } catch (err) {
      sessionsListEl.innerHTML = `<div class="loading-placeholder">Failed to load sessions</div>`;
    }
  }

  function renderSessionsList(sessions) {
    if (!sessions || sessions.length === 0) {
      sessionsListEl.innerHTML = `<div class="loading-placeholder">No sessions found</div>`;
      return;
    }

    sessionsListEl.innerHTML = "";
    sessions.forEach((s) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `session-item ${s.id === currentSessionId ? "active" : ""}`;
      item.dataset.id = s.id;

      const title = s.title || "Untitled Session";
      const snippet = s.last_snippet || "No recordings yet";
      const turns = s.turn_count || 0;
      const duration = s.total_audio_duration ? `${s.total_audio_duration.toFixed(1)}s` : "0s";

      item.innerHTML = `
        <div class="session-item-top">
          <span class="session-item-title">${escapeHtml(title)}</span>
          <span class="session-item-turns">${turns} turns</span>
        </div>
        <div class="session-item-snippet">${escapeHtml(snippet)}</div>
        <div class="session-item-meta">
          <span>${formatDate(s.updated_at)}</span>
          <span>${escapeHtml(s.clientId)} · ${duration}</span>
        </div>
      `;

      item.addEventListener("click", () => {
        selectSession(s.id);
      });

      sessionsListEl.appendChild(item);
    });
  }

  // Select and display a session
  async function selectSession(sessionId, scrollBottom = true) {
    if (isRecording || btnRecord.disabled) return;
    currentSessionId = sessionId;

    // Highlight in sidebar
    document.querySelectorAll(".session-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.id === sessionId);
    });

    try {
      const session = await API.fetchSession(sessionId);
      currentSession = session;
      renderActiveSession(session, scrollBottom);
    } catch (err) {
      showToast("Error loading session details", "danger");
    }
  }

  function renderActiveSession(session, scrollBottom = true) {
    sessionHeaderEl.style.display = "flex";
    sessionTitleEl.textContent = session.title;
    sessionDateEl.textContent = `Created ${formatDate(session.created_at)}`;
    sessionStatsEl.textContent = `${session.turn_count} turns • ${(session.total_audio_duration || 0).toFixed(1)}s audio`;
    sessionClientBadgeEl.textContent = `${session.clientId} / ${session.client_type || "web"}`;

    // Turns
    const turns = session.turns || [];
    if (turns.length === 0) {
      emptyStateEl.style.display = "flex";
      turnsContainerEl.querySelectorAll(".turn-card").forEach((el) => el.remove());
    } else {
      emptyStateEl.style.display = "none";
      turnsContainerEl.querySelectorAll(".turn-card").forEach((el) => el.remove());

      turns.forEach((turn) => {
        const card = document.createElement("div");
        card.className = "turn-card";
        card.dataset.turnId = turn.id;

        const audioPlayerHtml = turn.audio_filename
          ? `<div class="turn-audio">
               <audio controls preload="none" src="${API.recordingUrl(turn.audio_filename)}"></audio>
             </div>`
          : "";

        card.innerHTML = `
          <div class="turn-header">
            <div class="turn-header-left">
              <span class="turn-number">#${turn.turn_index}</span>
              <span class="turn-time">${turn.timestamp}</span>
              <div class="turn-badges">
                ${turn.infer_duration ? `<span class="turn-badge">${turn.infer_duration}s infer</span>` : ""}
                ${turn.audio_duration ? `<span class="turn-badge">${turn.audio_duration.toFixed(1)}s audio</span>` : ""}
              </div>
            </div>
            <div class="turn-actions">
              <button class="btn-icon btn-copy-turn" title="Copy this turn">Copy</button>
              <button class="btn-icon btn-icon-danger btn-delete-turn" title="Delete turn">Delete</button>
            </div>
          </div>
          <div class="turn-text">${escapeHtml(turn.transcript)}</div>
          ${audioPlayerHtml}
        `;

        // Card copy button
        card.querySelector(".btn-copy-turn").addEventListener("click", () => {
          navigator.clipboard.writeText(turn.transcript);
          showToast("Turn copied to clipboard");
        });

        // Card delete button
        card.querySelector(".btn-delete-turn").addEventListener("click", async () => {
          const dialog = document.getElementById("turn-delete-dialog");
          dialog.returnValue = "cancel";
          dialog.addEventListener("close", async () => {
            if (dialog.returnValue !== "delete") return;
            try {
              await API.deleteTurn(turn.id, true);
              showToast("Turn deleted");
              await selectSession(currentSessionId, false);
              await loadSessionsList();
            } catch (err) { showToast(err.message, "danger"); }
          }, { once: true });
          dialog.showModal();
        });

        turnsContainerEl.appendChild(card);
      });

      if (scrollBottom) {
        turnsContainerEl.scrollTop = turnsContainerEl.scrollHeight;
      }
    }
  }

  // Record Button Toggle
  btnRecord.addEventListener("click", async () => {
    if (!isRecording) {
      try {
        await recorder.start();
        isRecording = true;
        btnRecord.classList.add("recording");
        recordStatusEl.textContent = "Recording...";
      } catch (err) {
        showToast(err.message, "danger");
      }
    } else {
      recorder.stop();
    }
  });

  // New Session Button
  btnNewSession.addEventListener("click", async () => {
    if (isRecording || btnRecord.disabled) return;
    try {
      const newSession = await API.createSession("New Voice Session", "web", true);
      showToast("Session created");
      await loadSessionsList();
      await selectSession(newSession.id);
    } catch (err) {
      showToast("Failed to create session", "danger");
    }
  });

  // Inline Title Editing
  sessionTitleEl.addEventListener("blur", async () => {
    if (!currentSessionId) return;
    const newTitle = sessionTitleEl.textContent.trim();
    if (newTitle && newTitle !== currentSession?.title) {
      try {
        await API.updateSession(currentSessionId, { title: newTitle });
        showToast("Title updated");
        await loadSessionsList();
      } catch (err) {
        showToast("Failed to rename session", "danger");
      }
    }
  });

  sessionTitleEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      sessionTitleEl.blur();
    }
  });

  // Copy Full Session Conversation
  btnCopyRaw.addEventListener("click", async () => {
    if (!currentSessionId) return;
    try {
      const rawText = await API.getRawText(currentSessionId, "plain");
      if (!rawText.trim()) {
        showToast("No text to copy", "warning");
        return;
      }
      await navigator.clipboard.writeText(rawText);
      showToast("Conversation copied to clipboard");
    } catch (err) {
      showToast("Failed to copy conversation", "danger");
    }
  });

  // Export Markdown for Obsidian
  btnExportMd.addEventListener("click", async () => {
    if (!currentSessionId) return;
    try {
      const mdText = await API.getRawText(currentSessionId, "markdown");
      const blob = new Blob([mdText], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cleanTitle = (currentSession?.title || "session").replace(/[^a-zA-Z0-9_-]/g, "_");
      a.download = `${cleanTitle}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("Exported Markdown note");
    } catch (err) {
      showToast("Export failed", "danger");
    }
  });

  // Delete Session (Modal)
  btnDeleteSession.addEventListener("click", () => {
    if (!currentSessionId || isRecording || btnRecord.disabled) return;
    deleteTargetSessionId = currentSessionId;
    deleteDialog.showModal();
  });

  btnConfirmDelete.addEventListener("click", async () => {
    if (!deleteTargetSessionId) return;
    const deleteAudio = deleteAudioCheckbox.checked;
    try {
      await API.deleteSession(deleteTargetSessionId, deleteAudio);
      deleteDialog.close();
      showToast("Session deleted", "danger");
      deleteTargetSessionId = null;

      // Reload sessions and select the first available
      const sessions = await API.fetchSessions();
      renderSessionsList(sessions);
      if (sessions.length > 0) {
        await selectSession(sessions[0].id);
      } else {
        currentSessionId = null;
        currentSession = null;
        sessionHeaderEl.style.display = "none";
        emptyStateEl.style.display = "flex";
        turnsContainerEl.querySelectorAll(".turn-card").forEach((el) => el.remove());
      }
    } catch (err) {
      showToast("Failed to delete session", "danger");
    }
  });

  // Live Search Filter
  let searchTimeout = null;
  sessionSearchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      loadSessionsList(sessionSearchInput.value.trim());
    }, 250);
  });

  // Utilities
  function escapeHtml(text) {
    if (!text) return "";
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return text.replace(/[&<>"']/g, (m) => map[m]);
  }

  function formatDate(dateStr) {
    if (!dateStr) return "";
    try {
      const d = new Date(dateStr.replace(" ", "T"));
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch {
      return dateStr;
    }
  }

  // Initial startup sequence
  async function init() {
    await checkServerHealth();
    await loadSessionsList();

    // Load active session by default
    try {
      const active = await API.fetchActiveSession();
      if (active) {
        await selectSession(active.id);
      }
    } catch (e) {
      // Fallback
    }

    // Periodic health check
    setInterval(checkServerHealth, 15000);
  }

  init();
});
