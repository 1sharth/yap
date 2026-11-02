#!/home/iro/.local/share/dictate-venv/bin/python
"""
Resident Interactive Dictation Tool for LearnHarness / Obsidian / Yap Platform
Keeps Faster-Whisper resident in your RTX 4080 GPU VRAM so every dictation
is transcribed with zero model reload latency.
"""

import sys
import os
import time
import shutil
import subprocess
import threading
import tempfile
import json
import argparse
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
THINKING_FILE = os.environ.get(
    "DICTATE_THINKING_FILE",
    os.path.join(PROJECT_DIR, "thinking.txt")
)
RECORDINGS_DIR = os.environ.get(
    "DICTATE_RECORDINGS_DIR",
    os.path.join(PROJECT_DIR, "recordings")
)

# ANSI styling
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
RESET = "\033[0m"

DAEMON_URL = "http://127.0.0.1:28473"
CLIENT_ID = os.environ.get("YAP_CLIENT_ID", "admin")


def load_env_file(path: str):
    """Simple parser to load KEY=VALUE from .env files into os.environ."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ and v:
                    os.environ[k] = v
    except Exception:
        pass


def copy_to_clipboard(text: str):
    """Copies text to clipboard using wl-copy, xclip, or xsel."""
    for cmd in [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p.communicate(input=text.encode("utf-8"))
            if p.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def save_thinking(text: str, audio_path: str = None):
    """Appends thinking trace and audio link to thinking.txt for session logs."""
    try:
        os.makedirs(os.path.dirname(THINKING_FILE), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(THINKING_FILE, "a", encoding="utf-8") as f:
            if audio_path:
                rel_path = os.path.relpath(audio_path, os.path.dirname(THINKING_FILE))
                f.write(f"\n--- {timestamp} | audio: {rel_path} ---\n{text}\n")
            else:
                f.write(f"\n--- {timestamp} ---\n{text}\n")
    except Exception:
        pass


def _preload_nvidia_libs():
    """Pre-load cuBLAS and cuDNN from site-packages so CTranslate2 links automatically."""
    import glob, ctypes
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


def is_daemon_ready() -> bool:
    """Checks if the singleton Whisper daemon is running and healthy."""
    try:
        req = urllib.request.Request(f"{DAEMON_URL}/health")
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ready"
    except Exception:
        return False


def ensure_daemon_running() -> bool:
    """Ensures the singleton Whisper daemon is active, starting it if needed."""
    if is_daemon_ready():
        return True

    sys.stdout.write(f"{CYAN}⚡ Starting singleton Whisper daemon on RTX 4080...{RESET} ")
    sys.stdout.flush()

    # Try starting via systemd user service first
    started = False
    try:
        res = subprocess.run(
            ["systemctl", "--user", "start", "whisper-dictate.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if res.returncode == 0:
            started = True
    except Exception:
        pass

    # Fallback to direct background spawn if systemd not available
    if not started:
        server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_server.py")
        try:
            subprocess.Popen(
                [sys.executable, server_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            started = True
        except Exception:
            pass

    # Wait for daemon to become ready (up to 8s)
    for _ in range(16):
        time.sleep(0.5)
        if is_daemon_ready():
            sys.stdout.write(f"{GREEN}✓ Ready!{RESET}\n")
            return True

    sys.stdout.write(f"{YELLOW}daemon startup timed out.{RESET}\n")
    return False


# -------------------------------------------------------------
# API Client Helpers
# -------------------------------------------------------------
def api_get_active_session() -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(f"{DAEMON_URL}/api/sessions/active", headers={"X-Client-Id": CLIENT_ID})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def api_create_session(title: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.dumps({"title": title, "client_type": "cli", "is_active": True}).encode("utf-8")
        req = urllib.request.Request(
            f"{DAEMON_URL}/api/sessions",
            data=data,
            headers={"Content-Type": "application/json", "X-Client-Id": CLIENT_ID},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def api_get_session(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(f"{DAEMON_URL}/api/sessions/{session_id}", headers={"X-Client-Id": CLIENT_ID})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def api_list_sessions() -> list:
    try:
        req = urllib.request.Request(f"{DAEMON_URL}/api/sessions?limit=10", headers={"X-Client-Id": CLIENT_ID})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []


def api_session_transcribe(session_id: str, wav_path: str) -> Dict[str, Any]:
    """Posts local WAV path to server session transcribe endpoint for zero-copy speed."""
    body = json.dumps({"wav_path": wav_path}).encode("utf-8")
    req = urllib.request.Request(
        f"{DAEMON_URL}/api/sessions/{session_id}/transcribe",
        data=body,
        headers={"Content-Type": "application/json", "X-Client-Id": CLIENT_ID},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def transcribe_fallback_daemon(wav_path: str) -> str:
    """Legacy endpoint fallback."""
    body = json.dumps({"wav_path": wav_path}).encode("utf-8")
    req = urllib.request.Request(
        f"{DAEMON_URL}/transcribe",
        data=body,
        headers={"Content-Type": "application/json", "X-Client-Id": CLIENT_ID},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        return result.get("text", "").strip()


def run_single_yap(
    use_daemon: bool,
    session_info: Optional[Dict[str, Any]],
    model,
    backend_name: str,
    deepgram_key: str,
    groq_key: str
):
    """Executes one recording and transcription turn."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
        wav_path = tmp_audio.name

    rec_proc = None
    try:
        rec_proc = subprocess.Popen(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "pulse", "-i", "default",
                "-ar", "16000", "-ac", "1",
                wav_path
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        stop_event = threading.Event()

        def visualizer():
            bars = [" ", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃"]
            start_time = time.time()
            idx = 0
            while not stop_event.is_set():
                elapsed = int(time.time() - start_time)
                mins = elapsed // 60
                secs = elapsed % 60
                wave = "".join([bars[(idx + j) % len(bars)] for j in range(5)])
                sys.stdout.write(
                    f"\r{RED}● RECORDING{RESET} {BOLD}{mins:02d}:{secs:02d}{RESET} "
                    f"{CYAN}{wave}{RESET}  {DIM}Press [ENTER] to stop & copy...{RESET}"
                )
                sys.stdout.flush()
                idx += 1
                time.sleep(0.12)

        anim_thread = threading.Thread(target=visualizer, daemon=True)
        anim_thread.start()

        # Wait for user to hit Enter
        try:
            input()
        except KeyboardInterrupt:
            stop_event.set()
            if rec_proc:
                rec_proc.terminate()
                rec_proc.wait()
            print(f"\n{YELLOW}(Yap cancelled){RESET}")
            return

        # Stop recording
        stop_event.set()
        if rec_proc:
            rec_proc.terminate()
            rec_proc.wait()

        # Check minimal audio length
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 2048:
            print(f"\n{YELLOW}Recording was too brief. Speak and press Enter when done.{RESET}")
            return

        t0 = time.time()
        sys.stdout.write(f"\r\033[K{CYAN}⚡ Transcribing...{RESET}\n")
        sys.stdout.flush()

        text = ""
        infer_time = 0.0
        saved_audio_name = None
        turn_info = None

        if use_daemon and session_info and session_info.get("id"):
            try:
                res = api_session_transcribe(session_info["id"], wav_path)
                text = res.get("text", "")
                infer_time = res.get("infer_duration", time.time() - t0)
                turn_info = res.get("turn")
                if turn_info:
                    saved_audio_name = turn_info.get("audio_filename")
            except Exception as e:
                # Fallback to legacy transcribe if session endpoint fails
                try:
                    text = transcribe_fallback_daemon(wav_path)
                    infer_time = time.time() - t0
                except Exception:
                    print(f"{YELLOW}Daemon communication error ({e}){RESET}")
        elif use_daemon:
            try:
                text = transcribe_fallback_daemon(wav_path)
                infer_time = time.time() - t0
            except Exception as e:
                print(f"{YELLOW}Daemon communication error ({e}){RESET}")
        elif model is not None:
            segments, _ = model.transcribe(wav_path, beam_size=1)
            text = " ".join([seg.text.strip() for seg in segments]).strip()
            infer_time = time.time() - t0

        if not text:
            print(f"{YELLOW}No speech detected.{RESET}\n")
            return

        # If not saved by daemon, save locally
        if not saved_audio_name:
            try:
                os.makedirs(RECORDINGS_DIR, exist_ok=True)
                timestamp_slug = time.strftime("%Y-%m-%d_%H-%M-%S")
                saved_audio_name = f"{timestamp_slug}.wav"
                saved_audio_path = os.path.join(RECORDINGS_DIR, saved_audio_name)
                counter = 1
                while os.path.exists(saved_audio_path):
                    saved_audio_name = f"{timestamp_slug}_{counter}.wav"
                    saved_audio_path = os.path.join(RECORDINGS_DIR, saved_audio_name)
                    counter += 1
                shutil.copy2(wav_path, saved_audio_path)
                save_thinking(text, saved_audio_path)
            except Exception:
                saved_audio_name = None

        copied = copy_to_clipboard(text)

        turn_str = f" [Turn #{turn_info['turn_index']}]" if turn_info else ""
        print(f"\n{GREEN}{BOLD}✨ Transcribed ({infer_time:.2f}s){turn_str}:{RESET}")
        print(f'"{text}"\n')
        if copied:
            print(f"{CYAN}📋 Copied to clipboard! (Ctrl+V into your application){RESET}")
        if saved_audio_name:
            print(f"{DIM}🎙️  Voice saved:  recordings/{saved_audio_name}{RESET}")
        print(f"{DIM}📝 Log updated:  {os.path.basename(THINKING_FILE)}{RESET}\n")

    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


def main():
    global CLIENT_ID
    parser = argparse.ArgumentParser(description="Yap — Faster-Whisper GPU Dictation & Conversations")
    parser.add_argument("-n", "--new", type=str, metavar="TITLE", help="Start a new named session")
    parser.add_argument("-s", "--session", type=str, metavar="ID", help="Attach to a specific session ID")
    parser.add_argument("-l", "--list", action="store_true", help="List recent sessions and exit")
    args = parser.parse_args()

    load_env_file(os.path.join(PROJECT_DIR, ".env"))
    CLIENT_ID = os.environ.get("YAP_CLIENT_ID", "admin")
    load_env_file("/mnt/NewVolume/obsidian_data/40_Research/learning_tool/.env")
    load_env_file("/mnt/NewVolume/obsidian_data/40_Research/learning_tool/learnharness/.env.local")

    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    use_daemon = ensure_daemon_running()

    if args.list:
        sessions = api_list_sessions() if use_daemon else []
        print(f"\n{BOLD}Recent Yap Sessions:{RESET}")
        for s in sessions:
            active_flag = f"{GREEN}*active*{RESET}" if s.get("is_active") else ""
            print(f" • {s['id']} | {s['title']} ({s.get('turn_count', 0)} turns) {active_flag}")
        print()
        sys.exit(0)

    # Session resolution
    session_info = None
    if use_daemon:
        if args.new:
            session_info = api_create_session(args.new)
        elif args.session:
            session_info = api_get_session(args.session)
        
        if not session_info:
            session_info = api_get_active_session()

    backend_name = "None"
    model = None

    if use_daemon:
        backend_name = "Faster-Whisper on RTX 4080 (Singleton Daemon)"
    else:
        has_faster_whisper = False
        try:
            import faster_whisper  # noqa
            has_faster_whisper = True
        except ImportError:
            pass

        if has_faster_whisper:
            backend_name = "Faster-Whisper (Direct Local)"
            sys.stdout.write(f"{CYAN}⚡ Loading Whisper into RTX 4080 VRAM...{RESET} ")
            sys.stdout.flush()
            _preload_nvidia_libs()
            from faster_whisper import WhisperModel
            try:
                model = WhisperModel("base.en", device="cuda", compute_type="float16")
            except Exception:
                model = WhisperModel("base.en", device="cpu", compute_type="int8")
            sys.stdout.write(f"{GREEN}✓ Ready!{RESET}\n")
        elif deepgram_key:
            backend_name = "Deepgram Nova-3"
        elif groq_key:
            backend_name = "Groq Whisper (Free Cloud)"
        else:
            print(f"{YELLOW}{BOLD}[Notice] No transcription backend configured.{RESET}")
            sys.exit(1)

    sess_title = session_info.get("title", "Active Session") if session_info else "Local"
    print(f"{BOLD}🎙️  Dictation Session Active [{backend_name}]{RESET}")
    print(f"{CYAN}📁 Session: {sess_title}{RESET} {DIM}(Web Portal: http://127.0.0.1:28473/){RESET}")
    print(f"{DIM}Press [ENTER] to start recording, speak, then [ENTER] to transcribe & copy.{RESET}")
    print(f"{DIM}Exit anytime by typing 'q' or pressing Ctrl+C.{RESET}\n")

    while True:
        try:
            sys.stdout.write(f"{CYAN}▶ Press [ENTER] to start recording (or 'q' to quit): {RESET}")
            sys.stdout.flush()
            choice = input()
            if choice.strip().lower() in ("q", "quit", "exit"):
                print(f"{DIM}Session closed.{RESET}")
                break

            run_single_yap(use_daemon, session_info, model, backend_name, deepgram_key, groq_key)

        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM}Session closed.{RESET}")
            break


if __name__ == "__main__":
    main()
