"""Local evidence qualification contract; never authorizes scraped assertions."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import quality
import radar_profile
import review

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
SOURCE = 'https://careers.example.com/jobs/analyst?cycle=2027'


def raw_listing(**changes):
    raw = dict(id='analyst-2027', cycle_id='2027', source_url=SOURCE, title='Product Analyst', organization='Example')
    raw.update(changes)
    return raw


def entry(**changes):
    values = {'country': ['India'], 'experience': 0, 'graduation_year': [2027],
              'branch': ['Civil Engineering'], 'conditions': 'not_applicable', 'availability': 'open'}
    quotes = {'country': 'Applicants based in India may apply.', 'experience': 'No prior professional experience required.',
              'graduation_year': 'Graduating in 2027.', 'branch': 'Civil Engineering graduates accepted.',
              'conditions': 'No additional eligibility restrictions.', 'availability': 'Applications are open for this role.'}
    result = dict(id='analyst-2027', source_url=SOURCE, cycle_id='2027', content_fingerprint=review.content_fingerprint(raw_listing()), authoritative=True,
                  checked_at=NOW.isoformat(), expires_at=(NOW+timedelta(days=10)).isoformat(),
                  facts={k: dict(value=v, quote=quotes[k], url=SOURCE, observed_at=NOW.isoformat()) for k,v in values.items()})
    result.update(changes)
    return result


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'private-reviews.json'
        env = patch.dict(os.environ, {'RADAR_REVIEW_FILE': str(self.path), 'RADAR_PROFILE_JSON': '{}'})
        env.start(); self.addCleanup(env.stop)
        review._CACHE.clear()

    def write(self, document=None):
        document = document or entry()
        self.path.write_text(json.dumps(dict(version=1,reviews={document['id']:document})),encoding='utf-8')

    def item(self, **changes):
        raw = dict(id='analyst-2027', cycle_id='2027', source_url=SOURCE, title='Product Analyst', organization='Example')
        raw.update(changes)
        return quality.normalize(raw, NOW)

    def test_scraped_assertions_never_promote(self):
        result = self.item(eligibility_state='eligible', availability_state='open', qualification=entry(),
                           verification=entry(), reviewed=True, evidence=[dict(field=k, **v) for k,v in entry()['facts'].items()])
        self.assertEqual(result['eligibility_state'],'unknown')
        self.assertEqual(result['availability_state'],'unknown')
        self.assertEqual(result['queue'],'review')
        self.assertIsNone(result['last_verified_at'])

    def test_complete_local_review_promotes_with_field_reasons(self):
        self.write()
        result = self.item()
        self.assertEqual((result['eligibility_state'],result['availability_state'],result['queue']),('eligible','open','best'))
        self.assertEqual(result['verification_state'],'reviewed')
        self.assertIn('Country: India included.',result['fit_reason'])
        self.assertEqual(result['last_verified_at'],NOW.isoformat())
        self.assertEqual(len(result['evidence']),6)
        self.assertNotIn('checked_at',result)
        self.assertNotIn('facts',result)

    def test_each_missing_criterion_stays_unknown(self):
        for field in ('country','experience','graduation_year','branch','conditions'):
            with self.subTest(field=field):
                e=entry();del e['facts'][field];self.write(e);review._CACHE.clear()
                result=self.item()
                self.assertEqual(result['eligibility_state'],'unknown')
                self.assertNotEqual(result['queue'],'best')
                self.assertIn(field.replace('_',' '),result['fit_reason'])

    def test_no_availability_evidence_is_not_open(self):
        e=entry();del e['facts']['availability'];self.write(e)
        result=self.item()
        self.assertEqual(result['eligibility_state'],'eligible')
        self.assertEqual(result['availability_state'],'unknown')
        self.assertNotEqual(result['queue'],'best')

    def test_each_explicit_mismatch_is_ineligible(self):
        for field,value in [('country',['Canada']),('experience',5),('graduation_year',[2026]),('branch',['Computer Science']),('conditions','unmet')]:
            with self.subTest(field=field):
                e=entry();e['facts'][field]['value']=value;self.write(e);review._CACHE.clear()
                result=self.item()
                self.assertEqual((result['eligibility_state'],result['queue']),('ineligible','watch'))

    def test_identity_url_and_cycle_must_match_exactly(self):
        self.write()
        for changes in [dict(id='different'),dict(source_url=SOURCE+'&new=true'),dict(cycle_id='2028'),dict(cycle_id=None)]:
            self.assertNotEqual(self.item(**changes)['queue'],'best')

    def test_expired_future_or_old_evidence_does_not_promote(self):
        for changes in [dict(checked_at=(NOW+timedelta(days=1)).isoformat()),
                        dict(checked_at=(NOW-timedelta(days=1)).isoformat(),expires_at=NOW.isoformat())]:
            e=entry(**changes)
            for f in e['facts'].values():f['observed_at']=e['checked_at']
            self.write(e);review._CACHE.clear()
            self.assertNotEqual(self.item()['queue'],'best')
        e=entry()
        for fact in e['facts'].values():fact['observed_at']=(NOW-timedelta(days=15)).isoformat()
        self.write(e);review._CACHE.clear()
        self.assertEqual(self.item()['eligibility_state'],'unknown')

    def test_malformed_or_untrusted_review_fails_closed(self):
        variants=[]
        e=entry(authoritative=False);variants.append(e)
        for field,value in [('quote',''),('url','https://board.example.net/x'),('observed_at','2026-10-24')]:
            e=entry();e['facts']['country'][field]=value;variants.append(e)
        e=entry();e['facts']['experience']['value']=True;variants.append(e)
        for e in variants:
            with self.assertRaises(ValueError):review.validate_entry(e)
        self.path.write_text('{bad json',encoding='utf-8')
        with self.assertRaises(ValueError):self.item()

    def test_file_cache_parses_once_and_invalidates(self):
        self.write()
        with patch.object(review,'_read_registry',wraps=review._read_registry) as read:
            self.item();self.item();self.item()
            self.assertEqual(read.call_count,1)
            e=entry();e['facts']['country']['value']=['Canada'];self.write(e)
            self.assertEqual(self.item()['eligibility_state'],'ineligible')
            self.assertEqual(read.call_count,2)
        self.path.unlink()
        self.assertEqual(self.item()['eligibility_state'],'unknown')

    def test_private_registry_must_be_outside_repo(self):
        for path in ['reviews.json',str(Path(quality.__file__).parent/'private-reviews.json')]:
            with patch.dict(os.environ,{'RADAR_REVIEW_FILE':path}):
                with self.assertRaises(ValueError):review.registry_path()

    def test_stale_and_undated_are_distinct(self):
        old=(NOW-timedelta(days=15)).isoformat()
        result=self.item(last_seen_at=old)
        self.assertEqual((result['queue'],result['availability_state']),('watch','stale'))
        self.assertIn('Freshness not confirmed',self.item()['fit_reason'])
        self.assertNotIn('Freshness not confirmed',self.item(last_seen_at=NOW.isoformat())['fit_reason'])
        with patch.dict(os.environ,{'RADAR_PROFILE_JSON':'{"stale_days": 30}'}):
            self.assertEqual(self.item(last_seen_at=old)['queue'],'review')

    def test_old_listing_is_not_refreshed_by_eligibility_review_alone(self):
        e=entry();del e['facts']['availability'];self.write(e)
        result=self.item(last_seen_at=(NOW-timedelta(days=30)).isoformat())
        self.assertEqual((result['availability_state'],result['queue']),('stale','watch'))
        self.assertIn('Stale',result['freshness_note'])

    def test_profile_overrides_are_applied_to_each_fact(self):
        e=entry()
        e['facts']['country']['value']=['Canada'];e['facts']['experience']['value']=4
        e['facts']['graduation_year']['value']=[2026];e['facts']['branch']['value']=['Mechanical']
        self.write(e)
        with patch.dict(os.environ,{'RADAR_PROFILE_JSON':json.dumps(dict(country='Canada',max_experience=5,graduation_year=2026,branch='Mechanical'))}):
            self.assertEqual(self.item()['queue'],'best')
        for bad in ['null','{"max_experience":true}','{"branch":""}','{"include_engineering":"false"}','{"unknown":1}']:
            with patch.dict(os.environ,{'RADAR_PROFILE_JSON':bad}):
                with self.assertRaises(ValueError):radar_profile.get_profile()

    def test_engineering_is_optional_not_discarded(self):
        self.write(entry(content_fingerprint=review.content_fingerprint(raw_listing(title='Software Engineer'))))
        self.assertEqual(self.item(title='Software Engineer')['queue'],'explore')
        with patch.dict(os.environ,{'RADAR_PROFILE_JSON':'{"include_engineering":true}'}):
            self.assertEqual(self.item(title='Software Engineer')['queue'],'best')

    def test_deadline_after_review_still_closes(self):
        e=entry(checked_at=(NOW-timedelta(days=2)).isoformat())
        for fact in e['facts'].values():fact['observed_at']=e['checked_at']
        e['content_fingerprint']=review.content_fingerprint(raw_listing(deadline_at=(NOW-timedelta(days=1)).isoformat()))
        self.write(e)
        result=self.item(deadline_at=(NOW-timedelta(days=1)).isoformat())
        self.assertEqual((result['availability_state'],result['queue']),('closed','watch'))

    def test_newer_source_can_supersede_historical_exception(self):
        for title in ['Startup India Seed Fund Scheme','L&T Build India Scholarship','NIDHI-PRAYAS']:
            self.write(entry(content_fingerprint=review.content_fingerprint(raw_listing(title=title))))
            review._CACHE.clear()
            result=self.item(title=title)
            self.assertEqual((result['eligibility_state'],result['availability_state'],result['queue']),('eligible','open','best'))
        self.write(entry(content_fingerprint=review.content_fingerprint(raw_listing(title='Startup India Seed Fund Scheme'))))
        result=self.item(title='Startup India Seed Fund Scheme')
        self.assertIsNone(result['deadline_at'])
        self.assertNotIn('Closed',result['days_left_str'])

    def test_older_evidence_cannot_supersede_historical_exception(self):
        e=entry(checked_at='2026-10-23T17:00:00+00:00')
        for f in e['facts'].values():f['observed_at']=e['checked_at']
        for title,field,state in [('Startup India Seed Fund Scheme','availability_state','closed'),('L&T Build India Scholarship','eligibility_state','ineligible')]:
            e['content_fingerprint']=review.content_fingerprint(raw_listing(title=title));self.write(e);review._CACHE.clear()
            self.assertEqual(self.item(title=title)[field],state)

    def test_store_rejects_new_cycle_for_existing_id(self):
        now=datetime.now(timezone.utc)
        e=entry(checked_at=(now-timedelta(seconds=2)).isoformat(),expires_at=(now+timedelta(days=1)).isoformat())
        for f in e['facts'].values():f['observed_at']=e['checked_at']
        review.store_review(e)
        self.assertEqual(review.load_reviews()[e['id']]['facts'],e['facts'])
        e['cycle_id']='2028'
        with self.assertRaises(ValueError):review.store_review(e)

    def test_content_changes_invalidate_review_but_fetch_metadata_does_not(self):
        self.write()
        for change in [dict(title='Different Analyst'),dict(organization='Other'),dict(location='Canada'),
                       dict(description='Restricted role'),dict(deadline='2026-12-31'),dict(apply_url=SOURCE+'/new'),
                       dict(compensation='100 USD / hour')]:
            with self.subTest(change=change):
                self.assertNotEqual(self.item(**change)['queue'],'best')
        self.assertEqual(self.item(last_seen_at=NOW.isoformat(),fetched_at=NOW.isoformat())['queue'],'best')

    def test_cli_writes_reads_and_validates(self):
        now=datetime.now(timezone.utc)
        e=entry(checked_at=(now-timedelta(seconds=2)).isoformat(),expires_at=(now+timedelta(days=1)).isoformat())
        for f in e['facts'].values():f['observed_at']=e['checked_at']
        source=Path(self.tmp.name)/'input.json';source.write_text(json.dumps(e),encoding='utf-8')
        script=str(Path(review.__file__))
        written=subprocess.run([sys.executable,script,'put',str(source)],text=True,capture_output=True)
        self.assertEqual(written.returncode,0,written.stderr)
        self.assertIn(e['id'],json.loads(self.path.read_text())['reviews'])
        validated=subprocess.run([sys.executable,script,'validate'],text=True,capture_output=True)
        self.assertEqual(validated.returncode,0,validated.stderr)
        self.assertIn('Valid registry: 1 reviews',validated.stdout)


if __name__=='__main__':unittest.main()
