"""Offline official-source, dedupe and stable-storage regressions."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import ats
import scanner
import storage


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        override = patch.object(storage, 'DB_FILE', str(Path(self.temp.name) / 'test.db'))
        override.start()
        self.addCleanup(override.stop)
        storage.init_db()

    def config(self, data):
        path = Path(self.temp.name) / 'boards.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return patch.dict(os.environ, RADAR_BOARDS_FILE=str(path))

    def test_config_validation(self):
        b = dict(provider='greenhouse', token='example', company='Example')
        with self.config([b]):
            self.assertEqual(ats.configured_boards(), [b])
        for data in ([dict(b, token='../x')], [dict(b, provider='url')], [b] * 6, [b,b], {}):
            with self.subTest(data=data), self.config(data), self.assertRaises(ValueError):
                ats.configured_boards()

    def test_greenhouse_contract_and_no_false_eligibility(self):
        fetch = MagicMock(return_value={'jobs': [dict(id=1,title='Associate Product Manager',absolute_url='https://boards.greenhouse.io/example/jobs/1', location={'name':'India'},content='&lt;p&gt;Open to graduates&lt;/p&gt;')], 'meta':{'total':1}})
        board = dict(provider='greenhouse', token='example', company='Example')
        record = ats.fetch_board(board, fetch)[0]
        self.assertIn('?content=true', fetch.call_args.args[0])
        self.assertEqual(record['kind'], 'job')
        self.assertEqual(record['location'], 'India')
        self.assertEqual(record['availability_state'], 'unknown')
        import quality
        self.assertEqual(quality.normalize(record)['eligibility_state'], 'unknown')
        fetch.return_value['meta']['total'] = 2
        with self.assertRaises(ValueError):
            ats.fetch_board(board, fetch)

    def test_lever_contract_salary_eu_and_apply_url(self):
        fetch = MagicMock(return_value=[dict(id='one',text='Data Analyst',hostedUrl='https://jobs.eu.lever.co/example/one',applyUrl='https://jobs.eu.lever.co/example/one/apply', categories={'location':'Bangalore'},descriptionPlain='Analyze data',lists=[{'text':'Skills','content':'<p>SQL</p>'}], salaryRange={'min':50000,'max':60000,'currency':'EUR','interval':'year'})])
        record = ats.fetch_board(dict(provider='lever_eu',token='example',company='Example'), fetch)[0]
        self.assertIn('api.eu.lever.co',fetch.call_args.args[0])
        self.assertIn('SQL',record['description'])
        self.assertEqual(record['compensation'], '50,000 – 60,000 EUR / year')
        self.assertTrue(record['apply_url'].endswith('/apply'))

    def test_canonical_dedupe_no_fuzzy_merge(self):
        base = dict(id='a',source='A',source_url='https://example.com/job/1?utm_source=A',title='Analyst',organization='Example',verification_state='unverified')
        records = [base,dict(base,id='b',source='B',source_url='https://example.com/job/1#top'),dict(base,id='c',source_url='https://example.com/job/2'),dict(base,id='d',title='Manager')]
        results = storage.deduplicate_public(records)
        self.assertEqual(len(results),3)
        one = next(r for r in results if r['id']=='a')
        self.assertEqual(one['sources'], ['A','B'])
        self.assertEqual(one['duplicate_count'],1)
        self.assertNotEqual(storage.canonical_listing_url('https://example.com/job?id=1'),storage.canonical_listing_url('https://example.com/job?id=2'))

    def test_content_hash_ignores_fetch_time_not_real_changes(self):
        record = dict(id='test',title='Analyst',organization='Example',action_url='https://example.com/jobs/1',fetched_at='2026-01-01')
        def digest():
            with storage.connection() as c:
                return c.execute("SELECT content_hash FROM opportunities WHERE id='test'").fetchone()[0]
        storage.upsert_opportunity(record)
        one = digest()
        storage.upsert_opportunity(dict(record,fetched_at='2026-01-02'))
        self.assertEqual(one,digest())
        storage.upsert_opportunity(dict(record,title='Product Manager'))
        self.assertNotEqual(one,digest())

    def test_malformed_record_does_not_poison_source(self):
        fake_db = MagicMock()
        fake_db.get_all_active_opportunities.return_value=[]
        good = dict(id='good',title='Analyst',organization='Example')
        with patch.object(scanner,'db',fake_db), patch.object(scanner,'configured_adapters',return_value=[('One',lambda:[dict(good,organization=''),good])]):
            result = scanner.run_full_scan()
        self.assertEqual(result['counts']['One'],1)
        self.assertEqual(result['status'],'partial')
        fake_db.upsert_opportunity.assert_called_once_with(good)
        self.assertEqual(fake_db.record_scan.call_args.args[1],'partial')

    def test_lever_malformed_optional_sections_and_bad_destination(self):
        board = dict(provider='lever',token='example',company='Example')
        good = dict(id='good',text='Data Analyst',hostedUrl='https://jobs.lever.co/example/good',lists=7)
        bad = dict(good,id='bad',hostedUrl='https://attacker.example/apply')
        result = ats.fetch_board(board,lambda url:[bad,good])
        self.assertEqual([r['id'] for r in result],['lever_example_good'])
        with patch.object(storage,'get_scanner_health',return_value=[dict(status='error',last_success=None)]):
            from server import app
            self.assertEqual(app.test_client().get('/ready').status_code,503)

    def test_health_distinguishes_liveness_and_data(self):
        from server import app
        from quality import utcnow
        client = app.test_client()
        with patch.object(storage, 'get_scanner_health', return_value=[dict(status='success',last_success=utcnow())]):
            self.assertEqual(client.get('/health').get_json()['discovery_status'], 'current')
        with patch.object(storage, 'get_scanner_health', return_value=[dict(status='error',last_success=utcnow())]):
            data = client.get('/health').get_json()
            self.assertEqual(data['discovery_status'], 'degraded')
            self.assertEqual(data['status'], 'healthy')
        with patch.object(storage, 'get_scanner_health', return_value=[dict(status='not_run',last_success=None)]):
            self.assertEqual(client.get('/health').get_json()['discovery_status'], 'stale')

    def test_personal_tracks_are_discoverable(self):
        self.assertEqual(scanner._match('Graduate Civil Engineer')[0], 'Core Civil & Infrastructure')
        self.assertEqual(scanner._match('Strategy Analyst')[0], 'Consulting & Strategy')
        self.assertEqual(scanner._match('Marketing Manager'), ('', ''))

    def test_configured_adapters_have_independent_health(self):
        b = dict(provider='greenhouse',token='example',company='Example')
        fake_db = MagicMock()
        fake_db.get_all_active_opportunities.return_value=[]
        with self.config([b]), patch.object(scanner,'db',fake_db), patch.object(scanner,'fetch_remoteok',return_value=[]), patch.object(scanner,'fetch_jobicy',return_value=[]), patch.object(scanner,'fetch_unstop_opportunities',return_value=[]), patch.object(ats,'fetch_board',side_effect=ValueError('malformed employer feed')):
            result = scanner.run_full_scan()
        self.assertEqual(result['status'],'partial')
        self.assertIn('greenhouse:example',result['errors'])
        self.assertEqual(fake_db.record_scan.call_count,4)


if __name__ == '__main__':
    unittest.main()
