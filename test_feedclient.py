"""Offline stdlib transport, redirect, retry and persistent-cache tests."""
import io
import json
import os
import socket
import sqlite3
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import feedclient as client

URL = 'https://remoteok.com/api'


def headers(values=None):
    message = Message()
    for name, value in (values or {}).items():
        message[name] = str(value)
    return message


class Response(io.BytesIO):
    def __init__(self, body=b'{"jobs": [1]}', status=200, fields=None, url=URL):
        super().__init__(body)
        self.status = status
        self.headers = headers(fields)
        self.url = url
        self.read_sizes = []

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def info(self):
        return self.headers

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class FakeOpener:
    def __init__(self, *events):
        self.events = list(events)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


def error(status, fields=None):
    return urllib.error.HTTPError(URL, status, 'test', headers(fields), io.BytesIO())


class FeedClientTests(unittest.TestCase):
    def setUp(self):
        # Never create cache binaries inside the repository.
        scratch = os.environ.get('TMPDIR') or str(Path.home() / 'AppData/Local/hermes/cache/scratch')
        self.directory = tempfile.TemporaryDirectory(prefix='feedclient-', dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.cache = str(Path(self.directory.name) / 'cache.sqlite3')
        self.sleeps = []

    def fetch(self, transport, **kwargs):
        return client.fetch_json_with_metadata(URL, opener=transport, cache_path=self.cache,
                                               sleeper=self.sleeps.append, **kwargs)

    def count(self):
        with closing(sqlite3.connect(self.cache)) as connection:
            return connection.execute('SELECT COUNT(*) FROM http_cache').fetchone()[0]

    def test_json_only_api_and_request_bounds(self):
        response = Response()
        transport = FakeOpener(response)
        value = client.fetch_json(URL, opener=transport.open, cache_path=self.cache,
                                  sleeper=self.sleeps.append)
        self.assertEqual(value, {'jobs': [1]})
        request, timeout = transport.requests[0]
        self.assertEqual(timeout, 15)
        self.assertEqual(request.get_header('Accept'), 'application/json')
        self.assertEqual(response.read_sizes, [client.MAX_RESPONSE_BYTES + 1])
        self.assertTrue(response.closed)

    def test_exact_domain_allowlist(self):
        for host in client.ALLOWED_DOMAINS:
            self.assertEqual(client.validate_url('https://' + host + '/jobs'), 'https://' + host + '/jobs')
        unsafe = ['http://remoteok.com/api', 'https://remoteok.com.evil.test/api',
                  'https://sub.remoteok.com/api', 'https://127.0.0.1/',
                  'https://remoteok.com@localhost/', 'https://user@remoteok.com/',
                  'https://remoteok.com:444/', 'https://remoteok.com./api',
                  'https://remoteok.com/api#fragment', 'https://remoteok.com/\napi',
                  ' https://remoteok.com/api', 'file:///etc/passwd',
                  'https://remoteok.com\\@evil.test/', 'https://remoteok.com:bad/']
        for url in unsafe:
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.validate_url(url)

    def test_invalid_initial_url_never_calls_opener(self):
        transport = FakeOpener()
        with self.assertRaises(ValueError):
            client.fetch_json('https://localhost/', opener=transport, cache_path=self.cache)
        self.assertEqual(transport.requests, [])
        self.assertFalse(Path(self.cache).exists())

    def test_redirect_validation_before_follow(self):
        handler = client.SafeRedirectHandler()
        request = urllib.request.Request(URL)
        for status in (301, 302, 303, 307, 308):
            for url in ('http://remoteok.com/api', 'https://localhost/private',
                        'https://remoteok.com.evil.test/', 'file:///etc/passwd'):
                with self.subTest(status=status, url=url), self.assertRaises(ValueError):
                    handler.redirect_request(request, None, status, '', headers(), url)
            redirect = handler.redirect_request(request, None, status, '', headers(),
                                                'https://jobicy.com/api')
            self.assertEqual(redirect.full_url, 'https://jobicy.com/api')

    def test_secure_opener_blocks_redirect_before_any_second_request(self):
        # Exercise urllib's real redirect dispatch with a fake HTTPS wire handler.
        class Wire(urllib.request.HTTPSHandler):
            def __init__(self):
                super().__init__()
                self.urls = []

            def https_open(self, request):
                self.urls.append(request.full_url)
                response = Response(status=302, fields={'Location': 'https://localhost/secret'})
                response.code = 302
                response.msg = 'Found'
                return response

        wire = Wire()
        opener = urllib.request.build_opener(wire, client.SafeRedirectHandler())
        with self.assertRaises(ValueError):
            opener.open(URL, timeout=15)
        self.assertEqual(wire.urls, [URL])

    def test_cross_origin_redirect_drops_conditional_headers(self):
        request = urllib.request.Request(URL, headers={'If-None-Match': '"abc"',
                                                       'If-Modified-Since': 'yesterday'})
        redirected = client.SafeRedirectHandler().redirect_request(
            request, None, 302, '', headers(), 'https://jobicy.com/api')
        self.assertFalse(redirected.has_header('If-none-match'))
        self.assertFalse(redirected.has_header('If-modified-since'))
        same = client.SafeRedirectHandler().redirect_request(
            request, None, 302, '', headers(), 'https://remoteok.com/other')
        self.assertEqual(same.get_header('If-none-match'), '"abc"')

    def test_default_opener_keeps_tls_validation(self):
        contexts = []
        original = ssl.create_default_context

        def capture():
            context = original()
            contexts.append(context)
            return context

        with patch.object(client.ssl, 'create_default_context', side_effect=capture):
            opener = client.build_secure_opener()
        self.assertTrue(contexts[0].check_hostname)
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(any(isinstance(item, client.SafeRedirectHandler) for item in opener.handlers))

    def test_conditional_headers_and_304_persist_original_content_time(self):
        first_transport = FakeOpener(Response(fields={'ETag': '"abc"', 'Last-Modified': 'Wed, 21 Oct 2026 07:28:00 GMT'}))
        with patch.object(client, '_now', return_value='2026-10-21T07:28:00+00:00'):
            first, original = self.fetch(first_transport)
        not_modified = error(304, {'ETag': '"updated"'})
        transport = FakeOpener(not_modified)
        with patch.object(client, '_now', return_value='2026-10-22T07:28:00+00:00'):
            second, metadata = self.fetch(transport)
        self.assertEqual(first, second)
        self.assertTrue(not_modified.closed)
        self.assertEqual(metadata['status'], 304)
        self.assertTrue(metadata['from_cache'])
        self.assertEqual(metadata['content_fetched_at'], original['content_fetched_at'])
        self.assertNotEqual(metadata['checked_at'], original['checked_at'])
        request = transport.requests[0][0]
        self.assertEqual(request.get_header('If-none-match'), '"abc"')
        self.assertEqual(request.get_header('If-modified-since'), 'Wed, 21 Oct 2026 07:28:00 GMT')
        third = FakeOpener(error(304))
        self.fetch(third)
        self.assertEqual(third.requests[0][0].get_header('If-none-match'), '"updated"')
        self.assertEqual(self.count(), 1)

    def test_response_object_304_is_supported(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"abc"'})))
        value, metadata = self.fetch(FakeOpener(Response(b'', status=304)))
        self.assertEqual(value, {'jobs': [1]})
        self.assertTrue(metadata['from_cache'])

    def test_304_without_cache_is_error_not_empty_success(self):
        with self.assertRaisesRegex(ValueError, '304'):
            self.fetch(FakeOpener(error(304)))
        self.assertEqual(self.count(), 0)

    def test_304_without_validator_is_rejected(self):
        self.fetch(FakeOpener(Response()))
        with self.assertRaisesRegex(ValueError, '304'):
            self.fetch(FakeOpener(error(304)))

    def test_fresh_200_replaces_old_payload_and_validators(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        value, metadata = self.fetch(FakeOpener(Response(b'[]')))
        self.assertEqual(value, [])
        self.assertFalse(metadata['from_cache'])
        with closing(sqlite3.connect(self.cache)) as connection:
            self.assertIsNone(connection.execute('SELECT etag FROM http_cache').fetchone()[0])

    def test_cache_path_environment_override(self):
        with patch.dict(os.environ, {'RADAR_HTTP_CACHE': self.cache}):
            client.fetch_json(URL, opener=FakeOpener(Response()))
        self.assertEqual(self.count(), 1)

    def test_default_cache_is_sibling_file(self):
        with patch.dict(os.environ, {'RADAR_HTTP_CACHE': ''}):
            self.assertEqual(client._cache_path(None), str(Path(client.__file__).with_name('radar-http-cache.sqlite3')))

    def test_no_store_never_persists_and_evicts_old_cache(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        value, _ = self.fetch(FakeOpener(Response(b'[]', fields={'Cache-Control': 'private, No-Store'})))
        self.assertEqual(value, [])
        self.assertEqual(self.count(), 0)
        transport = FakeOpener(Response(fields={'Cache-Control': 'no-store'}))
        self.fetch(transport)
        self.assertIsNone(transport.requests[0][0].get_header('If-none-match'))
        self.assertEqual(self.count(), 0)

    def test_no_store_on_304_evicts_cached_representation(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        value, metadata = self.fetch(FakeOpener(error(304, {'Cache-Control': 'no-store'})))
        self.assertEqual(value, {'jobs': [1]})
        self.assertTrue(metadata['from_cache'])
        self.assertEqual(self.count(), 0)

    def test_retryable_http_statuses_recover_with_bounded_retry_after(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                self.sleeps.clear()
                failure = error(status, {'Retry-After': '5000'})
                transport = FakeOpener(failure, Response())
                _, metadata = self.fetch(transport)
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(metadata['attempts'], 2)
                self.assertEqual(self.sleeps, [5])
                self.assertTrue(failure.closed)

    def test_retry_after_date_invalid_negative_and_nonfinite(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(days=1))
        for value, expected in ((future, 5), ('nonsense', 1), ('-5', 0), ('inf', 1), ('NaN', 1)):
            with self.subTest(value=value):
                self.assertEqual(client._retry_delay(headers({'Retry-After': value}), 0), expected)

    def test_http_retries_exhaust_after_three_attempts_no_stale_fallback(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        failures = [error(503) for _ in range(3)]
        transport = FakeOpener(*failures)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch(transport)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(len(transport.requests), 3)
        self.assertEqual(self.sleeps, [1, 2])
        self.assertTrue(all(failure.closed for failure in failures))

    def test_no_store_retry_discards_validators_and_in_memory_cache(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        transport = FakeOpener(error(503, {'Cache-Control': 'no-store'}), error(304))
        with self.assertRaisesRegex(ValueError, '304'):
            self.fetch(transport)
        self.assertIsNone(transport.requests[1][0].get_header('If-none-match'))
        self.assertEqual(self.count(), 0)

    def test_cross_origin_304_cannot_revalidate_original_representation(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        with self.assertRaisesRegex(ValueError, '304'):
            self.fetch(FakeOpener(Response(b'', status=304, url='https://jobicy.com/api')))

    def test_corrupt_persistent_cache_is_explicit_failure(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        with closing(sqlite3.connect(self.cache)) as connection:
            with connection:
                connection.execute('UPDATE http_cache SET body = ?', (b'broken',))
        transport = FakeOpener()
        with self.assertRaises(ValueError):
            self.fetch(transport)
        self.assertEqual(transport.requests, [])

    def test_nonretryable_http_errors_fail_immediately(self):
        for status in (400, 401, 403, 404, 500, 501):
            with self.subTest(status=status):
                transport = FakeOpener(error(status))
                with self.assertRaises(urllib.error.HTTPError):
                    self.fetch(transport)
                self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_transient_transport_retries(self):
        failures = [urllib.error.URLError(socket.timeout('timeout')), ConnectionResetError('reset')]
        transport = FakeOpener(*failures, Response())
        self.fetch(transport)
        self.assertEqual(self.sleeps, [1, 2])
        self.assertEqual(len(transport.requests), 3)

    def test_exhausted_transport_does_not_return_stale_cache(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        transport = FakeOpener(*(TimeoutError('timeout') for _ in range(3)))
        with self.assertRaises(TimeoutError):
            self.fetch(transport)
        self.assertEqual(len(transport.requests), 3)

    def test_tls_failures_not_retried_or_hidden_by_cache(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        for failure in (ssl.SSLCertVerificationError('bad cert'),
                        urllib.error.URLError(ssl.SSLCertVerificationError('bad cert'))):
            with self.subTest(failure=failure):
                transport = FakeOpener(failure)
                with self.assertRaises((ssl.SSLError, urllib.error.URLError)):
                    self.fetch(transport)
                self.assertEqual(len(transport.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_payload_limit_enforced_with_or_without_content_length(self):
        for response in (Response(fields={'Content-Length': client.MAX_RESPONSE_BYTES + 1}),
                         Response(b' ' * (client.MAX_RESPONSE_BYTES + 1))):
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, 'size limit'):
                self.fetch(FakeOpener(response))
            self.assertTrue(response.closed)
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.sleeps, [])

    def test_exact_payload_limit_is_accepted(self):
        body = b'[]' + b' ' * (client.MAX_RESPONSE_BYTES - 2)
        value, _ = self.fetch(FakeOpener(Response(body)))
        self.assertEqual(value, [])

    def test_invalid_json_does_not_poison_previous_cache(self):
        self.fetch(FakeOpener(Response(fields={'ETag': '"old"'})))
        for body in (b'not-json', b'\xff'):
            with self.subTest(body=body), self.assertRaises((ValueError, UnicodeError)):
                self.fetch(FakeOpener(Response(body)))
        value, _ = self.fetch(FakeOpener(error(304)))
        self.assertEqual(value, {'jobs': [1]})

    def test_injected_opener_final_unsafe_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.fetch(FakeOpener(Response(url='https://localhost/private')))
        self.assertEqual(self.count(), 0)


if __name__ == '__main__':
    unittest.main()
