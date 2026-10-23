"""Regression cases discovered by independent review."""
import unittest
from datetime import datetime, timezone
import quality

class BoundaryTests(unittest.TestCase):
    def test_public_projection(self):
        o=quality.normalize(dict(title='Analyst',applicant_email='secret@example.com',raw_email={'body':'secret'},description='private input',payload='secret'))
        for key in ('applicant_email','raw_email','description','payload'):
            self.assertNotIn(key,o)
    def test_privacy_policy(self):
        self.assertFalse(quality.is_public_record(dict(source='Campus placement portal')))
        self.assertFalse(quality.is_public_record(dict(source='Example',visibility='private')))
        self.assertFalse(quality.is_public_record(dict(source='Example',is_private=True)))
        self.assertTrue(quality.is_public_record(dict(source='RemoteOK')))
    def test_canonical_deadline_preserved(self):
        o=quality.normalize(dict(title='Analyst',deadline_at='2027-01-01T00:00:00Z'))
        self.assertEqual(o['deadline_at'],'2027-01-01T00:00:00+00:00')
    def test_same_day_india_not_closed(self):
        now=datetime(2026,10,24,12,tzinfo=timezone.utc)
        o=quality.normalize(dict(title='Analyst',deadline='2026-10-24',deadline_timezone='Asia/Kolkata'),now)
        self.assertEqual(o['availability_state'],'unknown')
        self.assertIn('23:59:59+05:30',o['deadline_at'])
    def test_unknown_date_zone_not_assumed(self):
        self.assertIsNone(quality.normalize(dict(title='Analyst',deadline='2026-10-24'))['deadline_at'])
    def test_invalid_date_safe(self):
        self.assertIsNone(quality.normalize(dict(title='Analyst',deadline='2026-13-44'))['deadline_at'])
    def test_prizes_and_other_currency(self):
        for field,value in [('prizes','10,000 INR'),('compensation','50,000 EUR / year')]:
            o=quality.normalize(dict(title='Challenge',compensation=value,compensation_inr=value if field=='prizes' else None,evidence=[dict(field=field,quote=value,url='https://example.com')]))
            self.assertEqual(o['compensation'],value)
            if field=='prizes':self.assertEqual(o['compensation_inr'],value)
if __name__=='__main__':unittest.main()
