"""Ownership regression tests; temp data and a fake model, no GPU or real recordings.
Run with the project's Python plus httpx: python -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="yap-tests-")
os.environ['YAP_DB_PATH'] = str(Path(TEMP.name) / 'legacy.db')
os.environ['YAP_RECORDINGS_DIR'] = str(Path(TEMP.name) / 'recordings')
os.environ['YAP_THINKING_FILE'] = str(Path(TEMP.name) / 'thinking.txt')
# Start with the previous schema, including a turn that must survive migration.
with sqlite3.connect(os.environ['YAP_DB_PATH']) as conn:
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          client_type TEXT DEFAULT 'web', is_active INTEGER DEFAULT 0);
        CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
          turn_index INTEGER NOT NULL, timestamp TEXT NOT NULL, transcript TEXT NOT NULL,
          audio_filename TEXT, audio_duration REAL DEFAULT 0, infer_duration REAL DEFAULT 0,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE);
        INSERT INTO sessions VALUES ('legacy', 'Existing notes', '2026-01-01', '2026-01-01', 'cli', 1);
        INSERT INTO turns (session_id,turn_index,timestamp,transcript)
          VALUES ('legacy',1,'2026-01-01','Preserved transcript');
    """)

fake_whisper = types.ModuleType('faster_whisper')
class FakeModel:
    def __init__(self, *args, **kwargs):
        pass
    def transcribe(self, *args, **kwargs):
        return [types.SimpleNamespace(text='Test transcript')], None
fake_whisper.WhisperModel = FakeModel
with patch.dict(sys.modules, {'faster_whisper': fake_whisper}):
    import whisper_server as server
import db
from fastapi.testclient import TestClient


class ClientOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = TestClient(server.app)
        for client_id in ['alice', 'bob']:
            db.create_client(client_id, client_id.title())

    def setUp(self):
        self.alice = db.create_session('Alice private', is_active=True, client_id='alice')
        self.bob = db.create_session('Bob private', is_active=True, client_id='bob')
        self.audio = Path(db.RECORDINGS_DIR) / f"{self.alice['id']}.wav"
        with wave.open(str(self.audio), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x00\x00' * 1600)
        self.turn = db.add_turn(self.alice['id'], 'Private transcript', self.audio.name, client_id='alice')

    def request(self, method, path, client='alice', **kwargs):
        return self.api.request(method, path, headers={'X-Client-Id': client}, **kwargs)

    def test_migration_is_idempotent_and_preserves_data(self):
        db.init_db()
        db.init_db()
        legacy = db.get_session('legacy')
        self.assertEqual(legacy['clientId'], 'admin')
        self.assertEqual(legacy['turns'][0]['transcript'], 'Preserved transcript')
        self.assertEqual(db.get_client('admin')['clientName'], 'Administrator')
        with db.get_db_connection() as conn:
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE sessions SET clientId='unknown' WHERE id='legacy'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE sessions SET clientId=NULL WHERE id='legacy'")

    def test_fresh_database_has_seed_and_required_foreign_key(self):
        with patch.object(db, 'DB_PATH', str(Path(TEMP.name) / 'fresh.db')):
            db.init_db()
            session = db.create_session('Fresh session')
            self.assertEqual(session['clientId'], 'admin')
            with self.assertRaises(sqlite3.IntegrityError):
                db.create_session('Unregistered', client_id='unknown')
            with db.get_db_connection() as conn:
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_database_mutators_reject_foreign_owner(self):
        session_id = self.alice['id']
        self.assertIsNone(db.get_session(session_id, 'bob'))
        self.assertIsNone(db.update_session(session_id, title='Stolen', client_id='bob'))
        self.assertFalse(db.delete_session(session_id, client_id='bob'))
        self.assertFalse(db.delete_turn(self.turn['id'], client_id='bob'))
        with self.assertRaises(ValueError):
            db.add_turn(session_id, 'Injected', client_id='bob')
        self.assertTrue(self.audio.exists())

    def test_parallel_turn_writes_keep_unique_indices(self):
        from concurrent.futures import ThreadPoolExecutor
        def add(index):
            return db.add_turn(self.alice['id'], f'Turn {index}', client_id='alice')
        with ThreadPoolExecutor(max_workers=4) as pool:
            turns = list(pool.map(add, range(12)))
        self.assertEqual(len({turn['turn_index'] for turn in turns}), 12)
        self.assertEqual(db.get_session(self.alice['id'])['turn_count'], 13)

    def test_explicit_identity_and_client_registration(self):
        self.assertEqual(self.api.get('/api/sessions').status_code, 400)
        self.assertEqual(self.request('GET', '/api/sessions', 'unknown').status_code, 403)
        self.assertEqual(self.request('GET', '/api/sessions?clientId=bob').status_code, 400)
        payload = {'clientId': 'test-device', 'clientName': 'Test device'}
        self.assertEqual(self.request('POST', '/api/clients', json=payload).status_code, 403)
        self.assertEqual(self.request('POST', '/api/clients', 'admin', json=payload).status_code, 201)
        self.assertEqual(self.request('POST', '/api/clients', 'admin', json=payload).status_code, 409)
        self.assertEqual(len(self.request('GET', '/api/clients').json()), 1)

    def test_listing_search_and_creation_are_scoped(self):
        for query in ['', '?search=private', '?search=Private%20transcript', '?limit=1&offset=1']:
            rows = self.request('GET', '/api/sessions' + query).json()
            self.assertTrue(all(row['clientId'] == 'alice' for row in rows))
        self.assertEqual(self.request('GET', '/api/sessions?search=Bob').json(), [])
        result = self.request('POST', '/api/sessions', json={'title': 'New', 'clientId': 'bob'}).json()
        self.assertEqual(result['clientId'], 'alice')
        admin_rows = self.request('GET', '/api/sessions', 'admin').json()
        self.assertTrue({'admin', 'alice', 'bob'} <= {row['clientId'] for row in admin_rows})

    def test_foreign_session_operations_are_rejected(self):
        path = '/api/sessions/' + self.alice['id']
        cases = [('GET', path, {}), ('GET', path + '/raw', {}),
                 ('PATCH', path, {'json': {'title': 'Stolen', 'is_active': True}}),
                 ('DELETE', path, {}),
                 ('POST', path + '/transcribe', {'json': {'wav_path': str(self.audio)}}),
                 ('POST', path + '/upload', {'files': {'file': ('test.wav', b'bad audio')}})]
        with patch.object(server, 'transcribe_wav', side_effect=AssertionError('must reject before inference')):
            for method, url, kwargs in cases:
                with self.subTest(method=method, url=url):
                    self.assertEqual(self.request(method, url, 'bob', **kwargs).status_code, 404)
        self.assertEqual(db.get_session(self.alice['id'])['title'], 'Alice private')
        self.assertTrue(self.audio.exists())
        for method, url, kwargs in cases[:3]:
            self.assertEqual(self.request(method, url, 'admin', **kwargs).status_code, 200)

    def test_turn_and_recording_access_including_head_and_ranges(self):
        path = '/api/recordings/' + self.audio.name
        for method in ['GET', 'HEAD']:
            self.assertEqual(self.request(method, path, 'bob').status_code, 404)
            self.assertEqual(self.request(method, path).status_code, 200)
            self.assertEqual(self.request(method, path, 'admin').status_code, 200)
        self.assertEqual(self.api.get(path + '?clientId=alice', headers={'Range': 'bytes=0-43'}).status_code, 206)
        self.assertEqual(self.request('DELETE', '/api/turns/' + str(self.turn['id']), 'bob').status_code, 404)
        self.assertTrue(self.audio.exists())
        self.assertEqual(self.request('DELETE', '/api/turns/' + str(self.turn['id'])).status_code, 200)
        self.assertFalse(self.audio.exists())

    def test_active_sessions_remain_per_owner(self):
        first = self.request('GET', '/api/sessions/active').json()
        self.assertEqual(first['id'], self.alice['id'])
        self.assertTrue(db.get_session(self.bob['id'])['is_active'])
        extra = db.create_session('Other Alice session', client_id='alice')
        self.request('PATCH', '/api/sessions/' + extra['id'], 'admin', json={'is_active': True})
        self.assertFalse(db.get_session(self.alice['id'])['is_active'])
        self.assertTrue(db.get_session(extra['id'])['is_active'])
        self.assertTrue(db.get_session(self.bob['id'])['is_active'])

    def test_transcription_paths_uploads_and_legacy(self):
        bob_path = '/api/sessions/' + self.bob['id'] + '/transcribe'
        self.assertEqual(self.request('POST', bob_path, 'bob', json={'wav_path': str(self.audio)}).status_code, 404)
        self.assertEqual(self.request('POST', '/transcribe', json={'wav_path': str(self.audio)}).status_code, 403)
        self.assertEqual(self.request('POST', '/shutdown').status_code, 403)
        alice_path = '/api/sessions/' + self.alice['id']
        result = self.request('POST', alice_path + '/transcribe', json={'wav_path': str(self.audio)})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['text'], 'Test transcript')
        result = self.request('POST', alice_path + '/upload', files={'file': ('test.wav', self.audio.read_bytes(), 'audio/wav')})
        self.assertEqual(result.status_code, 200, result.text)
        with wave.open(str(Path(db.RECORDINGS_DIR) / result.json()['turn']['audio_filename'])) as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels()), (16000, 1))
        result = self.request('POST', '/api/sessions/missing/upload', files={'file': ('test.wav', b'bad')})
        self.assertEqual(result.status_code, 404)

    def test_delete_retaining_audio_removes_api_access(self):
        path = '/api/sessions/' + self.alice['id'] + '?delete_audio=false'
        self.assertEqual(self.request('DELETE', path, 'admin').status_code, 200)
        self.assertTrue(self.audio.exists())
        self.assertEqual(self.request('GET', '/api/recordings/' + self.audio.name, 'admin').status_code, 404)
        self.assertIsNone(db.get_session(self.alice['id']))


if __name__ == '__main__':
    unittest.main()
