/**
 * REST API client for Yap Platform
 */

const API = {
  clientId: "admin",

  request(url, options = {}) {
    return fetch(url, {
      ...options,
      headers: { ...options.headers, "X-Client-Id": this.clientId },
    });
  },

  recordingUrl(filename) {
    return `/api/recordings/${encodeURIComponent(filename)}?clientId=${encodeURIComponent(this.clientId)}`;
  },
  async checkHealth() {
    try {
      const res = await this.request("/api/health");
      return await res.json();
    } catch (e) {
      return { status: "offline", error: e.message };
    }
  },

  async fetchSessions(search = "") {
    const url = search ? `/api/sessions?search=${encodeURIComponent(search)}` : "/api/sessions";
    const res = await this.request(url);
    if (!res.ok) throw new Error("Failed to fetch sessions");
    return await res.json();
  },

  async fetchActiveSession() {
    const res = await this.request("/api/sessions/active");
    if (!res.ok) throw new Error("Failed to fetch active session");
    return await res.json();
  },

  async fetchSession(sessionId) {
    const res = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error("Failed to fetch session");
    return await res.json();
  },

  async createSession(title = "", clientType = "web", isActive = true) {
    const res = await this.request("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, client_type: clientType, is_active: isActive }),
    });
    if (!res.ok) throw new Error("Failed to create session");
    return await res.json();
  },

  async updateSession(sessionId, updates) {
    const res = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error("Failed to update session");
    return await res.json();
  },

  async deleteSession(sessionId, deleteAudio = true) {
    const res = await this.request(
      `/api/sessions/${encodeURIComponent(sessionId)}?delete_audio=${deleteAudio}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error("Failed to delete session");
    return await res.json();
  },

  async deleteTurn(turnId, deleteAudio = true) {
    const res = await this.request(
      `/api/turns/${turnId}?delete_audio=${deleteAudio}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error("Failed to delete turn");
    return await res.json();
  },

  async getRawText(sessionId, format = "plain") {
    const res = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}/raw?format=${format}`);
    if (!res.ok) throw new Error("Failed to get raw text");
    return await res.text();
  },

  async uploadAudio(sessionId, audioBlob) {
    const formData = new FormData();
    formData.append("file", audioBlob, "recording.webm");
    const res = await this.request(`/api/sessions/${encodeURIComponent(sessionId)}/upload`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Upload failed" }));
      throw new Error(err.detail || "Upload failed");
    }
    return await res.json();
  },
};

window.API = API;
