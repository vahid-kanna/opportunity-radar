"""Offline migration, process lease, and public-storage boundary regression tests."""
from contextlib import closing
import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import scanner
import storage


def _contend(path, ready, start, results, owner):
    storage.DB_FILE = path
    ready.put(owner)
    if not start.wait(20):
        raise RuntimeError('lease test start timed out')
    results.put(storage.acquire_scan_lease(owner))


class LeaseTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / 'AppData' / 'Local' / 'hermes' / 'cache' / 'scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=scratch)
        self.path = str(Path(self.tmp.name) / 'test.db')
        self.database = patch.object(storage, 'DB_FILE', self.path)
        self.database.start()
        self.seed = patch.object(storage, 'seed_existing_data')
        self.seed.start()

    def tearDown(self):
        self.seed.stop()
        self.database.stop()
        self.tmp.cleanup()

    def test_legacy_migration_and_original_backup_preserved(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('''CREATE TABLE opportunities (
                id TEXT PRIMARY KEY,type TEXT,title TEXT,organization TEXT,track TEXT,
                action_url TEXT,source TEXT,location TEXT,is_active INTEGER,
                discovered_at TEXT,last_verified_at TEXT)''')
            c.execute("INSERT INTO opportunities(id,title,organization,is_active) VALUES ('old','Original','Org',1)")
        storage.init_db()
        backup = Path(self.path + '.pre-v2.bak')
        original_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
        with closing(sqlite3.connect(str(backup))) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT title FROM opportunities').fetchone()[0], 'Original')
            self.assertNotIn('payload', [r[1] for r in c.execute('PRAGMA table_info(opportunities)')])
        storage.init_db()
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), original_hash)
        with storage.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT id FROM opportunities').fetchone()[0], 'old')
            self.assertIn('payload', [r[1] for r in c.execute('PRAGMA table_info(opportunities)')])
            self.assertEqual(c.execute('SELECT count(*) FROM scan_leases').fetchone()[0], 0)

    def test_future_version_refused_without_changes(self):
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute('CREATE TABLE future_only (value TEXT)')
            c.execute("INSERT INTO future_only VALUES ('keep')")
            c.execute('PRAGMA user_version=3')
        before = Path(self.path).read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Unsupported database schema version 3'):
            storage.init_db()
        self.assertEqual(Path(self.path).read_bytes(), before)
        self.assertFalse(Path(self.path + '.pre-v2.bak').exists())

    def test_version_two_adds_lease_table(self):
        storage.init_db()
        with storage.connection() as c:
            c.execute('DROP TABLE scan_leases')
        storage.init_db()
        self.assertTrue(storage.acquire_scan_lease('new'))

    def test_two_processes_only_one_winner(self):
        storage.init_db()
        ctx = multiprocessing.get_context('spawn')
        ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
        processes = [ctx.Process(target=_contend, args=(self.path, ready, start, results, str(i))) for i in range(2)]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                ready.get(timeout=20)
            start.set()
            outcomes = [results.get(timeout=20) for _ in processes]
            self.assertEqual(sorted(outcomes), [False, True])
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            ready.close()
            results.close()

    def test_expiry_and_owner_safe_release(self):
        storage.init_db()
        with patch('storage.time.time', return_value=100):
            self.assertTrue(storage.acquire_scan_lease('first', 10))
            self.assertFalse(storage.acquire_scan_lease('second', 10))
        with patch('storage.time.time', return_value=110):
            self.assertTrue(storage.acquire_scan_lease('second', 10))
            storage.release_scan_lease('first')
            self.assertFalse(storage.acquire_scan_lease('third', 10))
            storage.release_scan_lease('second')
            self.assertTrue(storage.acquire_scan_lease('third', 10))

    def test_busy_lease_never_calls_adapters(self):
        storage.init_db()
        self.assertTrue(storage.acquire_scan_lease('other-process'))
        with patch.object(scanner, 'fetch_remoteok') as remote, patch.object(scanner, 'fetch_jobicy') as jobicy, patch.object(scanner, 'fetch_unstop_opportunities') as unstop:
            self.assertEqual(scanner.run_full_scan()['status'], 'busy')
            remote.assert_not_called()
            jobicy.assert_not_called()
            unstop.assert_not_called()
        self.assertFalse(scanner.SCAN_LOCK.locked())
        with storage.connection() as c:
            self.assertEqual(c.execute('SELECT owner FROM scan_leases').fetchone()[0], 'other-process')

    def test_scan_releases_lease_on_success_and_failure(self):
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(scanner, 'fetch_remoteok', return_value=[]), patch.object(scanner, 'fetch_jobicy', return_value=[]), patch.object(scanner, 'fetch_unstop_opportunities', return_value=[]), patch.object(storage, 'get_all_active_opportunities', return_value=[], side_effect=RuntimeError('output failed') if failure else None):
                if failure:
                    with self.assertRaisesRegex(RuntimeError, 'output failed'):
                        scanner.run_full_scan()
                else:
                    self.assertEqual(scanner.run_full_scan()['status'], 'complete')
                self.assertFalse(scanner.SCAN_LOCK.locked())
                with storage.connection() as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM scan_leases').fetchone()[0], 0)

    def test_payload_cannot_bypass_privacy_boundary(self):
        storage.init_db()
        examples = [
            ('public', 'RemoteOK', {'source': 'RemoteOK'}),
            ('private_visibility', 'RemoteOK', {'source': 'RemoteOK', 'visibility': 'private'}),
            ('private_flag', 'RemoteOK', {'source': 'RemoteOK', 'is_private': True}),
            ('payload_mail', 'RemoteOK', {'source': 'Campus Mail'}),
            ('payload_unknown', 'RemoteOK', {'source': 'unrecognized campus feed'}),
            ('source_launder', 'IMAP', {'source': 'RemoteOK', 'visibility': 'public'}),
            ('unknown_campus', 'Campus', {'source': 'Campus'}),
        ]
        with storage.connection() as c:
            for ident, source, payload in examples:
                payload.update(id=ident, title='Software Engineer', organization='Org', action_url='https://example.org/job')
                c.execute('INSERT INTO opportunities(id,title,organization,source,action_url,payload) VALUES (?,?,?,?,?,?)', (ident, 'Software Engineer', 'Org', source, 'https://example.org/job', json.dumps(payload)))
        self.assertEqual([item['id'] for item in storage.get_all_active_opportunities()], ['public'])


if __name__ == '__main__':
    unittest.main()
