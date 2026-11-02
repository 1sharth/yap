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

## Client ownership (local use only)

**Deployment blocker:** client IDs are caller-supplied selectors, not credentials. Anyone
who sends `admin` can access every client's data and admin operations. This is intentional
for this local project. Before any deployment or network exposure, replace this trust
model with authenticated identities and server-side authorization; review local-path
transcription, CORS, shutdown, and the shared `thinking.txt` backup as well. Keep the
server bound to `127.0.0.1` until then. Do not treat these filters as a security boundary
against other users or processes on the same machine.

The `clients` table contains `clientId` and `clientName`; `sessions.clientId` references
it. Initialization seeds `admin` / `Administrator` and migrates existing sessions to
`admin` without changing their IDs, turns, or audio. Back up `yap.db` before upgrading.
The web dashboard explicitly uses `admin` and shows each session's owner. The CLI uses
`admin` by default; set `YAP_CLIENT_ID` to a registered client ID for a separate workspace.
`client_type` remains the input source (`web` / `cli`), separate from ownership.

Data API requests require `X-Client-Id: <id>`, or `?clientId=<id>` (used by browser audio
players). Missing IDs return 400; unknown IDs return 403. Conflicting header/query IDs
are rejected. Sessions are owned by the requesting client; payload fields cannot assign
another owner. Ordinary clients can list/search/read/export/update/delete only their own
sessions and turns, transcribe into their own sessions, and play their own recordings.
Inaccessible resources return 404. Admin can access all sessions; active-session selection
is per owner so activating one client's session does not deactivate another client's.
The legacy `/transcribe` and `/shutdown` endpoints require an explicit admin ID.
Deleted sessions' retained WAV files are no longer available through the API.

Register clients with admin (duplicate IDs return 409):

```bash
curl -X POST http://127.0.0.1:28473/api/clients \
  -H 'X-Client-Id: admin' -H 'Content-Type: application/json' \
  -d '{"clientId":"laptop","clientName":"Personal laptop"}'
curl -X POST http://127.0.0.1:28473/api/sessions \
  -H 'X-Client-Id: laptop' -H 'Content-Type: application/json' \
  -d '{"title":"Notes","is_active":true}'
YAP_CLIENT_ID=laptop yap
```

`GET /api/clients` lists all clients for admin, or the caller's own entry otherwise.

## Dashboard appearance

Yap follows `../BRAND_UI_UX.md`: warm paper / charcoal surfaces, **ink blue** as the
signature accent, Geist controls, Geist Mono metadata, and Newsreader transcript text
with local font fallbacks. The Appearance selector supports System (default), Light,
and Dark; explicit choices persist and System follows OS changes. The dashboard supports
keyboard session navigation, visible focus, reduced motion, and stacked mobile layouts.

## Ownership regression tests

With FastAPI, python-multipart, uvicorn, and httpx available in the Python environment:

```bash
python -m unittest discover -s tests -v
```

Tests use a temporary database/archive and a fake Whisper model, covering migration,
client scoping, admin access, audio ranges, uploads, and concurrent turn writes without
loading the GPU model or modifying local recordings.
