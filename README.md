# Yap — Local GPU Voice Dictation & Conversation Platform

A low-latency, private, local speech-to-text dictation engine, REST API, Web Portal Dashboard, and interactive CLI powered by **Faster-Whisper** on NVIDIA GPUs (CUDA float16).

---

## Architecture & Features

- **Singleton GPU Daemon ([`whisper_server.py`](whisper_server.py))**:
  - Serves a FastAPI REST API & Web Dashboard on `http://127.0.0.1:28473/`.
  - Keeps Faster-Whisper (`base.en`) permanently resident in GPU VRAM (~500 MB VRAM on RTX 4080).
  - Managed automatically by `systemd` user service ([`whisper-dictate.service`](whisper-dictate.service)).
  - Transcriptions complete in ~100ms.

- **Web Portal Dashboard (`/`)**:
  - Open `http://127.0.0.1:28473/` in any browser.
  - **Session Management**: Create, search, rename, and delete sessions (with confirmation dialog).
  - **Turn History & Playback**: Turn-by-turn transcripts with inline audio player and individual copy buttons.
  - **1-Click Conversation Export**: Copy formatted conversation to clipboard or download as Obsidian `.md` notes.
  - **In-Browser Dictation**: Record voice directly in the browser via the microphone button with real-time waveform visualizer.

- **SQLite Conversation Database ([`db.py`](db.py))**:
  - Tracks sessions, chronological turns, timestamps, audio filenames, and durations in `yap.db`.
  - Supports full session deletion (including cleaning up associated audio files).

- **Interactive Terminal CLI ([`dictate.py`](dictate.py))**:
  - Aliases: `dictate`, `yap`, or hotkey `Meta+Y`.
  - Syncs turns with the active session in `yap.db`.
  - Automatically copies transcripts to system clipboard (`wl-copy` / `xclip`).
  - Appends safety backup logs to [`thinking.txt`](thinking.txt) and archives WAVs in [`recordings/`](recordings/).

---

## Directory Layout

```text
/mnt/NewVolume/projects/yap/
├── db.py                      # SQLite database operations & session management
├── whisper_server.py          # FastAPI server (GPU Whisper + REST API + Web UI)
├── dictate.py                 # Interactive terminal CLI client
├── whisper-dictate.service    # Systemd user service unit
├── yap.db                     # Local SQLite database
├── recordings/                # Audio archive (YYYY-MM-DD_HH-MM-SS.wav)
│   └── README.md
├── web/                       # Web Portal Dashboard
│   ├── index.html             # Single-page dashboard UI
│   ├── css/style.css          # Responsive dark-theme styling
│   └── js/
│       ├── api.js             # REST API client
│       ├── recorder.js        # In-browser microphone recorder
│       └── app.js             # UI state & clipboard manager
├── thinking.txt               # Chronological transcript log with audio links
├── PLAN.md                    # Architecture and code structure specification
└── README.md                  # This file
```

---

## Usage

### 1. Web Portal Dashboard
Visit `http://127.0.0.1:28473/` in your browser to view all sessions, listen to recordings, export conversations, or dictate in-browser.

### 2. Terminal CLI
Run from any terminal:
```bash
dictate
# or
yap
```
- Press `[ENTER]` $\to$ Speak $\to$ Press `[ENTER]`.
- Transcribed text is copied to your clipboard instantly (`Ctrl+V` to paste).
- Type `q` or press `Ctrl+C` to quit.

#### CLI Session Flags:
- `yap --list`: List recent sessions.
- `yap --new "Meeting Name"`: Create and attach to a new named session.
- `yap --session <session_id>`: Attach to a specific session ID.

---

## REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` / `/api/health` | Model status, GPU device, and PID |
| `GET` | `/api/sessions` | List sessions with turn count and stats (`?search=...`) |
| `POST` | `/api/sessions` | Create a new session (`{"title": "..."}`) |
| `GET` | `/api/sessions/active` | Get or auto-create the active session |
| `GET` | `/api/sessions/{id}` | Get session details and all turn recordings |
| `PATCH` | `/api/sessions/{id}` | Rename session or toggle active status |
| `DELETE` | `/api/sessions/{id}` | Delete session (`?delete_audio=true` cleans up WAVs) |
| `GET` | `/api/sessions/{id}/raw` | Full conversation text (`?format=plain` or `?format=markdown`) |
| `POST` | `/api/sessions/{id}/transcribe` | Local zero-copy path transcription (`{"wav_path": "..."}`) |
| `POST` | `/api/sessions/{id}/upload` | Multipart audio upload from browser or remote clients |
| `GET` | `/api/recordings/{filename}` | Stream WAV audio with byte-range scrubber support |
| `DELETE` | `/api/turns/{id}` | Delete a single turn recording |

---

## Systemd Service Management

Check daemon status:
```bash
systemctl --user status whisper-dictate.service
```

Restart daemon:
```bash
systemctl --user restart whisper-dictate.service
```
