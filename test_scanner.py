"""Offline scanner regression tests: network and persistence are always mocked."""
import json
import ssl
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import scanner


class ScannerTests(unittest.TestCase):
    def feed(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return patch('scanner.urllib.request.urlopen', return_value=response)

    def remote(self, **extra):
        return dict(id=1, position='Software Engineer', company='Example',
                    url='https://remoteok.com/remote-jobs/1', **extra)

    def jobicy(self, **extra):
        return dict(id=2, jobTitle='Data Analyst', companyName='Example',
                    url='https://jobicy.com/jobs/2', **extra)

    def test_remote_unknowns_and_full_description(self):
        description = '<p>Build Python services.</p><p>' + 'detail ' * 1000 + '</p>'
        with self.feed([{'legal': 'metadata'}, self.remote(description=description)]):
            result = scanner.fetch_remoteok()[0]
        for field in ('location', 'compensation_usd', 'compensation_inr', 'eligibility', 'deadline', 'days_left_str', 'quadrant'):
            self.assertEqual(result[field], '', field)
        self.assertEqual(result['availability_state'], 'unknown')
        self.assertIn('detail ' * 999, result['description'])
        self.assertEqual(datetime.fromisoformat(result['fetched_at']).utcoffset().total_seconds(), 0)
        self.assertTrue(all(set(e) == {'field', 'quote', 'url'} for e in result['evidence']))

    def test_salary_only_source_values_no_fx(self):
        with self.feed([self.remote(salary_min='100000', salary_max=150000)]):
            result = scanner.fetch_remoteok()[0]
        self.assertEqual(result['compensation_usd'], '100,000 – 150,000 USD')
        self.assertEqual(result['compensation_inr'], '')
        self.assertTrue(any(e['field'] == 'compensation' for e in result['evidence']))

    def test_bad_salary_is_unknown(self):
        for value in (None, 'N/A', 'NaN', 'inf', -3, True, {'amount': 1}):
            with self.subTest(value=value), self.feed([self.remote(salary_min=value)]):
                self.assertEqual(scanner.fetch_remoteok()[0]['compensation_usd'], '')

    def test_tags_do_not_override_title_or_create_match(self):
        payload = [self.remote(tags=['product manager', 'llm']),
                   dict(self.remote(tags=['software engineer']), id=3, position='Sales representative')]
        with self.feed(payload):
            results = scanner.fetch_remoteok()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['track'], 'Engineering & Python')
        self.assertEqual(scanner._match('Email coordinator'), ('', ''))

    def test_jobicy_currency_not_assumed(self):
        with self.feed({'jobs': [self.jobicy(annualSalaryMin=50000, jobDescription='<p>Analyze risks.</p>')]}):
            result = scanner.fetch_jobicy()[0]
        self.assertEqual(result['compensation_usd'], '')
        self.assertEqual(result['description'], 'Analyze risks.')
        with self.feed({'jobs': [self.jobicy(annualSalaryMin=50000, salaryCurrency='EUR')]}):
            result = scanner.fetch_jobicy()[0]
        self.assertEqual(result['compensation'], '50,000+ EUR / year')
        self.assertEqual(result['compensation_usd'], '')

    def test_unstop_prizes_deadlines_and_no_default_track(self):
        items = [dict(id=1, title='AI Hackathon', seo_url='/competitions/ai-1',
                      description='<p>Build a model.</p>', end_date='2000-01-01T10:00:00+05:30',
                      prizes=[{'name': 'Winner', 'amount': 10000, 'currency': 'INR'}, {'unknown': 'x'}]),
                 dict(id=2, title='Case competition', seo_url='https://unstop.com/competitions/2', end_date='invalid'),
                 dict(id=3, title='Email writing', seo_url='https://unstop.com/competitions/3')]
        with self.feed({'data': {'data': items}}):
            results = scanner.fetch_unstop_opportunities()
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['availability_state'], 'closed')
        self.assertEqual(results[0]['deadline'], '2000-01-01T04:30:00+00:00')
        self.assertEqual(results[0]['compensation_inr'], 'Winner: 10,000 INR')
        self.assertEqual(results[0]['location'], '')
        self.assertEqual(results[1]['deadline'], '')
        self.assertEqual(results[1]['availability_state'], 'unknown')
        self.assertEqual(results[1]['compensation_inr'], '')
        self.assertEqual(scanner._prizes({'amount': 1000}), '')
        self.assertEqual(scanner._prizes(1000), '')

    def test_aware_dates_and_date_only(self):
        self.assertEqual(scanner._deadline('2027-01-01T00:30:00+05:30').isoformat(), '2026-12-31T19:00:00+00:00')
        self.assertEqual(scanner._deadline('2027-01-01').isoformat(), '2027-01-01T18:29:59+00:00')
        self.assertIsNone(scanner._deadline('someday'))

    def test_tls_and_request_bounds(self):
        self.assertEqual(scanner.CTX.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(scanner.CTX.check_hostname)
        with self.feed([]) as request:
            scanner.fetch_remoteok()
        self.assertEqual(request.call_args.kwargs['timeout'], 15)
        self.assertIs(request.call_args.kwargs['context'], scanner.CTX)
        request.return_value.__enter__.return_value.read.assert_called_once_with(scanner.MAX_RESPONSE_BYTES + 1)

    def test_oversized_and_failed_network_raise(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'x' * (scanner.MAX_RESPONSE_BYTES + 1)
        with patch('scanner.urllib.request.urlopen', return_value=response), self.assertRaises(ValueError):
            scanner.fetch_remoteok()
        for adapter in (scanner.fetch_remoteok, scanner.fetch_jobicy, scanner.fetch_unstop_opportunities):
            with patch('scanner.urllib.request.urlopen', side_effect=TimeoutError('offline')), self.assertRaises(TimeoutError):
                adapter()

    def test_malformed_feed_raises(self):
        for adapter in (scanner.fetch_remoteok, scanner.fetch_jobicy, scanner.fetch_unstop_opportunities):
            with self.feed({}), self.assertRaises(ValueError):
                adapter()

    def test_verify_url_does_not_fetch_arbitrary_urls(self):
        with patch('scanner.urllib.request.urlopen', side_effect=TimeoutError('offline')) as request:
            self.assertEqual(scanner.verify_url('https://example.org')[0], None)
            self.assertEqual(scanner.verify_url('javascript:alert(1)')[0], False)
            request.assert_not_called()

    def test_scan_partial_and_source_records(self):
        fake_db = MagicMock()
        fake_db.get_all_active_opportunities.return_value = [{}, {}]
        with patch.object(scanner, 'db', fake_db), patch.object(scanner, 'fetch_remoteok', side_effect=TimeoutError('offline')), patch.object(scanner, 'fetch_jobicy', return_value=[{'id': 'j'}]), patch.object(scanner, 'fetch_unstop_opportunities', return_value=[{'id': 'u'}]):
            result = scanner.run_full_scan()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['counts'], {'RemoteOK': 0, 'Jobicy': 1, 'Unstop': 1})
        self.assertEqual(fake_db.record_scan.call_count, 3)
        self.assertEqual(fake_db.upsert_opportunity.call_count, 2)
        self.assertEqual(fake_db.record_scan.call_args_list[0].args, ('RemoteOK', 'error', 0, 'TimeoutError: offline'))
        self.assertFalse(scanner.SCAN_LOCK.locked())

    def test_complete_empty_is_success_and_all_failed_is_error(self):
        for fail, status in [(False, 'complete'), (True, 'error')]:
            fake_db = MagicMock()
            fake_db.get_all_active_opportunities.return_value = []
            with patch.object(scanner, 'db', fake_db), patch.object(scanner, 'fetch_remoteok', return_value=[], side_effect=ValueError('bad') if fail else None), patch.object(scanner, 'fetch_jobicy', return_value=[], side_effect=ValueError('bad') if fail else None), patch.object(scanner, 'fetch_unstop_opportunities', return_value=[], side_effect=ValueError('bad') if fail else None):
                self.assertEqual(scanner.run_full_scan()['status'], status)
            self.assertEqual(fake_db.record_scan.call_count, 3)

    def test_scan_busy_and_release_on_init_failure(self):
        scanner.SCAN_LOCK.acquire()
        try:
            with patch.object(scanner, 'db') as fake_db:
                self.assertEqual(scanner.run_full_scan()['status'], 'busy')
                fake_db.init_db.assert_not_called()
        finally:
            scanner.SCAN_LOCK.release()
        with patch.object(scanner, 'db') as fake_db:
            fake_db.init_db.side_effect = RuntimeError('db unavailable')
            with self.assertRaises(RuntimeError):
                scanner.run_full_scan()
        self.assertFalse(scanner.SCAN_LOCK.locked())


if __name__ == '__main__':
    unittest.main()
