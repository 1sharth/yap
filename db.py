"""
Database layer for Yap Multi-Client Platform.
Uses SQLite with WAL mode for fast concurrent operations.
Manages sessions, turns, and audio metadata.
"""

import os
import sqlite3
import datetime
from typing import Optional, List, Dict, Any

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("YAP_DB_PATH", os.path.join(PROJECT_DIR, "yap.db"))
RECORDINGS_DIR = os.environ.get("YAP_RECORDINGS_DIR", os.path.join(PROJECT_DIR, "recordings"))


def get_db_connection() -> sqlite3.Connection:
    """Creates a thread-safe connection with dictionary cursor and WAL mode."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    """Initializes SQLite tables and indices."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    with get_db_connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            client_type TEXT DEFAULT 'web',
            is_active INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            transcript TEXT NOT NULL,
            audio_filename TEXT,
            audio_duration REAL DEFAULT 0.0,
            infer_duration REAL DEFAULT 0.0,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_turns_session_id ON turns(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
        """)
        conn.commit()


def generate_session_id() -> str:
    """Generates a human-friendly timestamped session ID."""
    now = datetime.datetime.now()
    return f"ses_{now.strftime('%Y%m%d_%H%M%S')}"


def create_session(
    title: Optional[str] = None,
    client_type: str = "web",
    is_active: bool = False
) -> Dict[str, Any]:
    """Creates a new session and returns its dict representation."""
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_id = generate_session_id()
    
    if not title or not title.strip():
        title = f"Session {datetime.datetime.now().strftime('%b %d, %H:%M')}"
    else:
        title = title.strip()

    with get_db_connection() as conn:
        if is_active:
            conn.execute("UPDATE sessions SET is_active = 0")
        
        conn.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at, client_type, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, title, now_iso, now_iso, client_type, 1 if is_active else 0)
        )
        conn.commit()

    return get_session(session_id)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves session details and all its turns."""
    with get_db_connection() as conn:
        cur = conn.execute(
            "SELECT id, title, created_at, updated_at, client_type, is_active FROM sessions WHERE id = ?",
            (session_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        session = dict(row)
        session["is_active"] = bool(session["is_active"])

        cur_turns = conn.execute(
            """
            SELECT id, session_id, turn_index, timestamp, transcript,
                   audio_filename, audio_duration, infer_duration
            FROM turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            """,
            (session_id,)
        )
        session["turns"] = [dict(t) for t in cur_turns.fetchall()]
        session["turn_count"] = len(session["turns"])
        session["total_audio_duration"] = sum(t["audio_duration"] or 0.0 for t in session["turns"])
        return session


def list_sessions(search: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Lists sessions ordered by updated_at with stats and last snippet."""
    with get_db_connection() as conn:
        if search and search.strip():
            query = f"%{search.strip()}%"
            cur = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.client_type, s.is_active,
                       COUNT(t.id) AS turn_count,
                       COALESCE(SUM(t.audio_duration), 0.0) AS total_audio_duration,
                       (SELECT t2.transcript FROM turns t2 WHERE t2.session_id = s.id ORDER BY t2.turn_index DESC LIMIT 1) AS last_snippet
                FROM sessions s
                LEFT JOIN turns t ON s.id = t.session_id
                WHERE s.title LIKE ? OR EXISTS (SELECT 1 FROM turns t3 WHERE t3.session_id = s.id AND t3.transcript LIKE ?)
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (query, query, limit, offset)
            )
        else:
            cur = conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.client_type, s.is_active,
                       COUNT(t.id) AS turn_count,
                       COALESCE(SUM(t.audio_duration), 0.0) AS total_audio_duration,
                       (SELECT t2.transcript FROM turns t2 WHERE t2.session_id = s.id ORDER BY t2.turn_index DESC LIMIT 1) AS last_snippet
                FROM sessions s
                LEFT JOIN turns t ON s.id = t.session_id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset)
            )
        
        results = []
        for row in cur.fetchall():
            item = dict(row)
            item["is_active"] = bool(item["is_active"])
            results.append(item)
        return results


def get_or_create_active_session() -> Dict[str, Any]:
    """Finds the active session, or most recent session, or creates a new one."""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT id FROM sessions WHERE is_active = 1 LIMIT 1")
        row = cur.fetchone()
        if row:
            session = get_session(row["id"])
            if session:
                return session

        # Fallback to the latest session if one exists
        cur_latest = conn.execute("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1")
        row_latest = cur_latest.fetchone()
        if row_latest:
            session_id = row_latest["id"]
            set_active_session(session_id)
            return get_session(session_id)

    # None exists, create a default active one
    return create_session(title="Quick Dictation", client_type="cli", is_active=True)


def set_active_session(session_id: str) -> bool:
    """Sets the given session as active, deactivating all others."""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not cur.fetchone():
            return False
        conn.execute("UPDATE sessions SET is_active = 0")
        conn.execute("UPDATE sessions SET is_active = 1 WHERE id = ?", (session_id,))
        conn.commit()
        return True


def update_session(
    session_id: str,
    title: Optional[str] = None,
    is_active: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    """Updates session title or active state."""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,))
        if not cur.fetchone():
            return None

        now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if title is not None:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip(), now_iso, session_id)
            )
        if is_active is not None:
            if is_active:
                conn.execute("UPDATE sessions SET is_active = 0")
                conn.execute("UPDATE sessions SET is_active = 1, updated_at = ? WHERE id = ?", (now_iso, session_id))
            else:
                conn.execute("UPDATE sessions SET is_active = 0, updated_at = ? WHERE id = ?", (now_iso, session_id))

        conn.commit()
    return get_session(session_id)


def delete_session(session_id: str, delete_audio: bool = True) -> bool:
    """Deletes a session, its turns, and optionally its audio recordings."""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT audio_filename FROM turns WHERE session_id = ?", (session_id,))
        turn_audios = [r["audio_filename"] for r in cur.fetchall() if r["audio_filename"]]

        res = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        deleted = res.rowcount > 0

    if deleted and delete_audio:
        for fname in turn_audios:
            fpath = os.path.join(RECORDINGS_DIR, fname)
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

    return deleted


def add_turn(
    session_id: str,
    transcript: str,
    audio_filename: Optional[str] = None,
    audio_duration: float = 0.0,
    infer_duration: float = 0.0
) -> Dict[str, Any]:
    """Adds a turn to a session. Auto-generates title if initial default session title."""
    transcript = transcript.strip()
    now_iso = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        # Verify or auto-create session
        cur = conn.execute("SELECT id, title FROM sessions WHERE id = ?", (session_id,))
        sess_row = cur.fetchone()
        if not sess_row:
            create_session(title=f"Session {datetime.datetime.now().strftime('%b %d, %H:%M')}", is_active=True)
            sess_row = conn.execute("SELECT id, title FROM sessions WHERE id = ?", (session_id,)).fetchone()

        # Determine turn index
        cur_idx = conn.execute(
            "SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_idx FROM turns WHERE session_id = ?",
            (session_id,)
        )
        next_idx = cur_idx.fetchone()["next_idx"]

        # Insert turn
        cur_ins = conn.execute(
            """
            INSERT INTO turns (session_id, turn_index, timestamp, transcript,
                               audio_filename, audio_duration, infer_duration)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, next_idx, now_iso, transcript, audio_filename, audio_duration, infer_duration)
        )
        turn_id = cur_ins.lastrowid

        # Auto-update session title if it's the first turn and has a generic name
        current_title = sess_row["title"] if sess_row else ""
        if next_idx == 1 and (current_title.startswith("Session ") or current_title == "Quick Dictation"):
            # Use the first 45 characters of speech as an intuitive title
            snippet = transcript.split(".")[0].strip()
            if len(snippet) > 45:
                snippet = snippet[:45].rsplit(" ", 1)[0] + "..."
            if snippet:
                conn.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (snippet, now_iso, session_id)
                )
            else:
                conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now_iso, session_id))
        else:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now_iso, session_id))

        conn.commit()

        cur_turn = conn.execute(
            """
            SELECT id, session_id, turn_index, timestamp, transcript,
                   audio_filename, audio_duration, infer_duration
            FROM turns WHERE id = ?
            """,
            (turn_id,)
        )
        return dict(cur_turn.fetchone())


def delete_turn(turn_id: int, delete_audio: bool = True) -> bool:
    """Deletes a single turn and optionally its audio file."""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT session_id, audio_filename FROM turns WHERE id = ?", (turn_id,))
        row = cur.fetchone()
        if not row:
            return False

        session_id = row["session_id"]
        audio_filename = row["audio_filename"]

        conn.execute("DELETE FROM turns WHERE id = ?", (turn_id,))
        conn.commit()

    if delete_audio and audio_filename:
        fpath = os.path.join(RECORDINGS_DIR, audio_filename)
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass

    return True


def get_session_raw_text(session_id: str, format_type: str = "plain") -> str:
    """
    Returns full conversation text ready for 1-click clipboard copying.
    format_type can be 'plain' or 'markdown'.
    """
    session = get_session(session_id)
    if not session or not session.get("turns"):
        return ""

    lines = []
    if format_type == "markdown":
        lines.append(f"# {session['title']}\n*Created: {session['created_at']}*\n")
        for turn in session["turns"]:
            lines.append(f"### Turn {turn['turn_index']} ({turn['timestamp']})")
            lines.append(f"{turn['transcript']}\n")
    else:
        for turn in session["turns"]:
            lines.append(f"[{turn['timestamp']}] {turn['transcript']}")

    return "\n".join(lines)


# Initialize DB on module load if needed
init_db()
