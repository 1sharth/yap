#!/home/iro/.local/share/dictate-venv/bin/python
"""
Yap Multi-Client Daemon & Platform Server
Keeps Faster-Whisper resident in RTX 4080 GPU VRAM (CUDA float16).
Serves REST API, audio streaming, session management, and Web Portal UI.
"""

import sys
import os
import time
import glob
import ctypes
import threading
import subprocess
import wave
import shutil
import tempfile
import sqlite3
import uuid
from typing import Optional

HOST = "127.0.0.1"
PORT = 28473

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.environ.get("YAP_RECORDINGS_DIR", os.path.join(PROJECT_DIR, "recordings"))
THINKING_FILE = os.environ.get("YAP_THINKING_FILE", os.path.join(PROJECT_DIR, "thinking.txt"))
WEB_DIR = os.path.join(PROJECT_DIR, "web")


def _preload_nvidia_libs():
    """Pre-load cuBLAS and cuDNN from site-packages so CTranslate2 links automatically."""
    venv_site = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)),
        "lib",
        f"python{sys.version_info.major}.{sys.version_info.minor}",
        "site-packages"
    )
    nvidia_base = os.path.join(venv_site, "nvidia")
    if os.path.exists(nvidia_base):
        for lib_dir in glob.glob(f"{nvidia_base}/*/lib"):
            for so in glob.glob(f"{lib_dir}/*.so*"):
                try:
                    ctypes.CDLL(so)
                except Exception:
                    pass


# Preload libs before importing faster_whisper
_preload_nvidia_libs()
from faster_whisper import WhisperModel
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, status, Header, Depends
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db

print("⚡ Loading Faster-Whisper into RTX 4080 VRAM (Singleton Daemon)...")
t0 = time.time()
try:
    model = WhisperModel("base.en", device="cuda", compute_type="float16")
    device_info = "cuda:float16"
except Exception as e:
    print(f"CUDA init failed ({e}), falling back to CPU")
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    device_info = "cpu:int8"

load_duration = time.time() - t0
print(f"✓ Whisper model ready in {load_duration:.2f}s ({device_info})")

infer_lock = threading.Lock()

app = FastAPI(
    title="Yap Dictation & Conversation Platform",
    description="Multi-client API for local Faster-Whisper dictation, audio storage, and session management",
    version="2.0.0"
)

# Enable CORS for local web & tool clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_audio_duration(wav_path: str) -> float:
    """Computes duration in seconds from WAV header."""
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return round(frames / float(rate), 2)
    except Exception:
        pass
    return 0.0


def append_to_thinking_log(text: str, audio_rel_path: Optional[str] = None):
    """Appends transcript and audio link to thinking.txt for safety & Obsidian embedding."""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(THINKING_FILE, "a", encoding="utf-8") as f:
            if audio_rel_path:
                f.write(f"\n--- {timestamp} | audio: {audio_rel_path} ---\n{text}\n")
            else:
                f.write(f"\n--- {timestamp} ---\n{text}\n")
    except Exception:
        pass


def transcribe_wav(wav_path: str) -> tuple[str, float]:
    """Transcribes a WAV file using the resident Whisper model under lock."""
    if not os.path.exists(wav_path):
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    t_start = time.time()
    with infer_lock:
        segments, _ = model.transcribe(wav_path, beam_size=1)
        text = " ".join([seg.text.strip() for seg in segments]).strip()
    duration = time.time() - t_start
    return text, round(duration, 3)


def request_client(
    header: Optional[str] = Header(None, alias="X-Client-Id"),
    query: Optional[str] = Query(None, alias="clientId"),
) -> str:
    """Local identity selector only, NOT authentication. Never deploy as-is."""
    if header is not None and query is not None and header != query:
        raise HTTPException(400, "Conflicting client IDs")
    client_id = header if header is not None else query
    if not client_id:
        raise HTTPException(400, "Provide X-Client-Id or clientId")
    if not db.get_client(client_id):
        raise HTTPException(403, "Unknown client ID")
    return client_id


def admin_client(client_id: str = Depends(request_client)) -> str:
    if client_id != "admin":
        raise HTTPException(403, "Admin client required")
    return client_id


def require_session(session_id: str, client_id: str):
    session = db.get_session(session_id, client_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def require_local_input(wav_path: str, client_id: str):
    # Existing archived audio cannot be read across clients via path transcription.
    real_path = os.path.realpath(wav_path)
    archive = os.path.realpath(RECORDINGS_DIR)
    if os.path.commonpath([real_path, archive]) == archive:
        if os.path.dirname(real_path) != archive or not db.can_access_recording(os.path.basename(real_path), client_id):
            raise HTTPException(404, "Recording not found")


# Request schemas
class LegacyTranscribeRequest(BaseModel):
    wav_path: str


class SessionCreateRequest(BaseModel):
    title: Optional[str] = None
    client_type: str = "web"
    is_active: bool = False


class SessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None


class LocalTranscribeRequest(BaseModel):
    wav_path: str


# -------------------------------------------------------------
# System & Compatibility Endpoints
# -------------------------------------------------------------
@app.get("/health")
@app.get("/api/health")
def health():
    return {
        "status": "ready",
        "model": "base.en",
        "device": device_info,
        "pid": os.getpid(),
        "recordings_dir": RECORDINGS_DIR,
        "db_path": db.DB_PATH
    }


@app.post("/transcribe")
def legacy_transcribe(req: LegacyTranscribeRequest, client_id: str = Depends(admin_client)):
    """Preserves 100% backward compatibility with legacy scripts."""
    if not os.path.exists(req.wav_path):
        raise HTTPException(status_code=400, detail="WAV file not found")
    try:
        text, duration = transcribe_wav(req.wav_path)
        return {"text": text, "duration": duration}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shutdown")
def shutdown(client_id: str = Depends(admin_client)):
    def _do_shutdown():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return {"status": "shutting_down"}


# -------------------------------------------------------------
# Session Management Endpoints
# -------------------------------------------------------------
class ClientCreateRequest(BaseModel):
    clientId: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    clientName: str = Field(min_length=1, max_length=200)


@app.post("/api/clients", status_code=201)
def create_client_endpoint(req: ClientCreateRequest, client_id: str = Depends(admin_client)):
    name = req.clientName.strip()
    if not name:
        raise HTTPException(422, "Client name cannot be blank")
    try:
        return db.create_client(req.clientId, name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Client ID already exists")


@app.get("/api/clients")
def list_clients_endpoint(client_id: str = Depends(request_client)):
    return db.list_clients(client_id)


@app.post("/api/sessions")
def create_session_endpoint(req: SessionCreateRequest, client_id: str = Depends(request_client)):
    session = db.create_session(
        title=req.title,
        client_type=req.client_type,
        is_active=req.is_active,
        client_id=client_id
    )
    return session


@app.get("/api/sessions")
def list_sessions_endpoint(
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    client_id: str = Depends(request_client)
):
    return db.list_sessions(search=search, limit=limit, offset=offset, client_id=client_id)


@app.get("/api/sessions/active")
def get_active_session_endpoint(client_id: str = Depends(request_client)):
    return db.get_or_create_active_session(client_id)


@app.get("/api/sessions/{session_id}")
def get_session_endpoint(session_id: str, client_id: str = Depends(request_client)):
    session = db.get_session(session_id, client_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.patch("/api/sessions/{session_id}")
def update_session_endpoint(session_id: str, req: SessionUpdateRequest, client_id: str = Depends(request_client)):
    session = db.update_session(session_id, title=req.title, is_active=req.is_active, client_id=client_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.delete("/api/sessions/{session_id}")
def delete_session_endpoint(session_id: str, delete_audio: bool = Query(True), client_id: str = Depends(request_client)):
    deleted = db.delete_session(session_id, delete_audio=delete_audio, client_id=client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@app.get("/api/sessions/{session_id}/raw")
def get_raw_session_text(session_id: str, format: str = Query("plain"), client_id: str = Depends(request_client)):
    """Returns conversation text formatted for easy 1-click clipboard copying."""
    require_session(session_id, client_id)
    text = db.get_session_raw_text(session_id, format_type=format, client_id=client_id)
    return PlainTextResponse(text)


@app.delete("/api/turns/{turn_id}")
def delete_turn_endpoint(turn_id: int, delete_audio: bool = Query(True), client_id: str = Depends(request_client)):
    deleted = db.delete_turn(turn_id, delete_audio=delete_audio, client_id=client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"deleted": True, "turn_id": turn_id}


# -------------------------------------------------------------
# Transcription & Recording Endpoints
# -------------------------------------------------------------
@app.post("/api/sessions/{session_id}/transcribe")
async def session_transcribe_local_path(session_id: str, req: LocalTranscribeRequest, client_id: str = Depends(request_client)):
    """
    Zero-copy local path transcription (used by terminal CLI for maximum speed).
    Expects JSON: {"wav_path": "/path/to/wav"}
    """
    require_session(session_id, client_id)
    wav_path = req.wav_path
    require_local_input(wav_path, client_id)
    if not os.path.exists(wav_path):
        raise HTTPException(status_code=400, detail="WAV file not found")

    text, infer_time = transcribe_wav(wav_path)
    if not text:
        return {"text": "", "turn": None, "infer_duration": infer_time}

    # Store WAV into recordings directory
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    saved_filename = f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex}.wav"
    saved_path = os.path.join(RECORDINGS_DIR, saved_filename)
    # Keep the local fast path for the CLI's already-normalized WAV.
    try:
        with wave.open(wav_path, "rb") as audio:
            normalized = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
    except (wave.Error, EOFError):
        normalized = False
    if normalized:
        shutil.copy2(wav_path, saved_path)
    else:
        result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
                                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", saved_path],
                                capture_output=True)
        if result.returncode:
            if os.path.exists(saved_path):
                os.remove(saved_path)
            raise HTTPException(400, "Audio conversion failed")
    audio_dur = get_audio_duration(saved_path)

    # A session might be deleted while inference is running.
    try:
        turn = db.add_turn(
            session_id=session_id,
            transcript=text,
            audio_filename=saved_filename,
            audio_duration=audio_dur,
            infer_duration=infer_time,
            client_id=client_id
        )
    except Exception:
        os.remove(saved_path)
        raise

    # Append to thinking.txt
    rel_audio = f"recordings/{saved_filename}" if saved_filename else None
    append_to_thinking_log(text, rel_audio)

    return {
        "text": text,
        "turn": turn,
        "infer_duration": infer_time,
        "audio_duration": audio_dur
    }


@app.post("/api/sessions/{session_id}/upload")
async def session_transcribe_upload(
    session_id: str,
    file: UploadFile = File(...),
    client_id: str = Depends(request_client)
):
    """
    Multipart audio file upload (used by Web Portal browser mic recording or remote clients).
    Accepts webm, wav, ogg, etc. Converts to 16 kHz Mono WAV using ffmpeg.
    """
    require_session(session_id, client_id)

    # Write upload to temp file
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_in:
        tmp_in_path = tmp_in.name
        content = await file.read()
        tmp_in.write(content)

    saved_path = None
    persisted = False
    try:
        # Target destination in recordings/
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        saved_filename = f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex}.wav"
        saved_path = os.path.join(RECORDINGS_DIR, saved_filename)

        # Convert to 16 kHz Mono WAV via ffmpeg
        conv_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", tmp_in_path,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            saved_path
        ]
        res = subprocess.run(conv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode != 0 or not os.path.exists(saved_path):
            raise HTTPException(status_code=400, detail=f"Audio conversion failed: {res.stderr.decode('utf-8', errors='ignore')}")

        text, infer_time = transcribe_wav(saved_path)
        audio_dur = get_audio_duration(saved_path)

        # Save to database
        turn = db.add_turn(
            session_id=session_id,
            transcript=text,
            audio_filename=saved_filename,
            audio_duration=audio_dur,
            infer_duration=infer_time,
            client_id=client_id
        )

        persisted = True

        # Append to thinking.txt
        append_to_thinking_log(text, f"recordings/{saved_filename}")

        return {
            "text": text,
            "turn": turn,
            "infer_duration": infer_time,
            "audio_duration": audio_dur
        }
    finally:
        if not persisted and saved_path and os.path.exists(saved_path):
            os.remove(saved_path)
        if os.path.exists(tmp_in_path):
            try:
                os.remove(tmp_in_path)
            except OSError:
                pass


# -------------------------------------------------------------
# Audio Playback & Streaming
# -------------------------------------------------------------
@app.api_route("/api/recordings/{filename}", methods=["GET", "HEAD"])
def stream_recording(filename: str, client_id: str = Depends(request_client)):
    """Streams audio file with native HTTP Range support for browser scrubbing."""
    if filename != os.path.basename(filename) or not db.can_access_recording(filename, client_id):
        raise HTTPException(404, "Recording not found")
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(RECORDINGS_DIR, safe_filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(file_path, media_type="audio/wav")


# -------------------------------------------------------------
# Web Portal Static Files
# -------------------------------------------------------------
if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.api_route("/", methods=["GET", "HEAD"])
def serve_index():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return PlainTextResponse("Yap Dictation Service running. Web portal under construction.")


def run_server():
    print(f"🚀 Starting Yap Platform Server on http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    run_server()
