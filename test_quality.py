import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone
import db
import quality

class QualityTests(unittest.TestCase):
    def item(self, **kw):
        return quality.normalize(dict(id='x', title='Associate Product Manager', organization='Example', action_url='https://example.com/job?signature=abc', **kw))
    def test_unknown_not_eligible(self):
        self.assertEqual(self.item(location='Germany')['eligibility_state'], 'unknown')
    def test_senior(self):
        o=quality.normalize(dict(title='Senior Backend Engineer', description='7+ years of professional experience',organization='DDG'))
        self.assertEqual(o['eligibility_state'],'ineligible')
        self.assertNotEqual(o['queue'],'best')
    def test_money(self):
        self.assertIsNone(self.item(compensation_inr='Est. ₹20–40 LPA equivalent')['compensation_inr'])
    def test_signed_url(self):
        self.assertTrue(self.item()['source_url'].endswith('?signature=abc'))
    def test_unsafe_urls(self):
        for url in ('javascript:alert(1)','http://127.0.0.1/a','http://169.254.169.254/','http://[::1]/','http://localhost/','https://x:y@example.com/'):
            self.assertEqual(quality.safe_url(url),'')
    def test_sisfs(self):
        o=quality.normalize(dict(title='Startup India Seed Fund Scheme',deadline='Rolling 2026 Cohort'))
        self.assertEqual(o['availability_state'],'closed')
    def test_lnt(self):
        o=quality.normalize(dict(title='L&T Build India Scholarship'))
        self.assertEqual(o['queue'],'watch')
        self.assertEqual(o['eligibility_state'],'ineligible')
    def test_prayas(self):
        o=quality.normalize(dict(title='NIDHI-PRAYAS'))
        self.assertEqual(o['eligibility_state'],'unknown')
        self.assertIn('Physical',o['fit_reason'])
    def test_resource(self):
        self.assertEqual(quality.normalize(dict(title='The Product Folks'))['queue'],'resources')
    def test_unicode(self):
        self.assertEqual(quality.clean_text('Sothebyâ\x80\x99s'),'Sotheby’s')
    def test_taxonomy(self):
        self.assertEqual(quality.classify('Business Development Manager','Product Management'),'other')
        self.assertEqual(quality.classify('Antler India Residency'),'funding')
        self.assertEqual(quality.classify('Ministry pilot','Railway Pilot / RailRaksha'),'funding')
    def test_no_rolling_fallback(self):
        self.assertEqual(self.item()['days_left_str'],'Deadline not verified')

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.patcher=patch.object(db,'DB_FILE',str(Path(self.tmp.name)/'test.db'))
        self.patcher.start()
        db.init_db()
    def tearDown(self):
        self.patcher.stop();self.tmp.cleanup()
    def test_idempotent_and_first_seen(self):
        o=dict(id='regression',title='Analyst',organization='Example',action_url='https://example.com/a')
        db.upsert_opportunity(o)
        a=next(x for x in db.get_all_active_opportunities() if x['id']=='regression')
        db.upsert_opportunity(o)
        b=[x for x in db.get_all_active_opportunities() if x['id']=='regression']
        self.assertEqual(len(b),1)
        self.assertEqual(a['discovered_at'],b[0]['discovered_at'])
        self.assertIsNone(b[0]['last_verified_at'])
    def test_health(self):
        db.record_scan('RemoteOK','success',3)
        db.record_scan('RemoteOK','error',0,'blocked')
        h=next(x for x in db.get_scanner_health() if x['source']=='RemoteOK')
        self.assertEqual(h['status'],'error')
        self.assertIsNotNone(h['last_success'])
    def test_auth(self):
        from server import app
        self.assertEqual(app.test_client().post('/api/scan').status_code,401)

if __name__=='__main__':unittest.main()
