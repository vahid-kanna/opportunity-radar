"""Offline browser checks for visible exports, sharing and honest status.
Run alongside the original six: uv run --with flask --with playwright python -m unittest test_ui test_ui_additions -v
"""
import csv
import io
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask import render_template, request
import test_ui as fixture

NOW = '2026-10-24T12:00:00Z'
HEALTH = [
    dict(source='Fresh source', status='success', count=3, last_success=NOW, last_attempt=NOW),
    dict(source='Failed recent source', status='error', error='Synthetic failure', count=0, last_success=NOW, last_attempt=NOW),
    dict(source='Old source', status='success', count=2, last_success='2026-10-20T12:00:00Z', last_attempt='2026-10-20T12:00:00Z'),
    dict(source='Unrun source', status='not_run', count=0),
    dict(source='Future source', status='success', count=0, last_success='2030-01-01T00:00:00Z', last_attempt='2030-01-01T00:00:00Z'),
]
EXPORT_ROWS = [
    dict(id='formula-1', title='=HYPERLINK("https://evil.test","x"),\nCSV', organization='  +SUM(1,2)', location='@NOW()', track_group='product', queue='review', fit_score=30, source_url='javascript:alert(1)', compensation='-42', raw_payload={'secret':'PRIVATE_RAW'}, evidence=[dict(field='x',quote='PRIVATE_EVIDENCE')]),
    dict(id='formula-2', title='-2+3', organization='\tspreadsheet', track_group='product', queue='review', fit_score=20, source_url='https://example.com/second'),
    dict(id='formula-3', title='@SUM(1)', organization='Safe, "quoted" organization', track_group='product', queue='review', fit_score=10),
    dict(id='excluded', title='Excluded grant', queue='watch', fit_score=99),
]

@fixture.app.route('/additions')
def additions():
    rows = EXPORT_ROWS if request.args.get('export') else fixture.FIXTURES
    return render_template('index.html', opportunities=rows, scanner_health=HEALTH, scan_enabled=False)


class RadarAdditions(unittest.TestCase):
    # Reuse the original real-browser/server lifecycle without duplicating tests.
    setUpClass = classmethod(fixture.RadarUI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.RadarUI.tearDownClass.__func__)
    setUp = fixture.RadarUI.setUp
    tearDown = fixture.RadarUI.tearDown

    def test_csv_visible_sorted_formula_safe_and_allowlisted(self):
        p = self.page
        p.goto(self.url + '/additions?export=1')
        p.locator('[data-id="formula-2"]').get_by_role('button', name='Hide:', exact=False).click()
        with p.expect_download() as pending:
            p.locator('#download-csv').click()
        download = pending.value
        self.assertEqual(download.suggested_filename, 'opportunity-radar-visible.csv')
        payload = Path(download.path()).read_text(encoding='utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['Title'], '\'=HYPERLINK("https://evil.test","x"),\nCSV')
        self.assertEqual(rows[0]['Organization'], "'  +SUM(1,2)")
        self.assertEqual(rows[0]['Location'], "'@NOW()")
        self.assertEqual(rows[0]['Pay / funding'], "'-42")
        self.assertEqual(rows[0]['Destination'], '')
        self.assertEqual(rows[1]['Title'], "'@SUM(1)")
        self.assertEqual(rows[1]['Organization'], 'Safe, "quoted" organization')
        self.assertEqual(rows[1]['Pay / funding'], 'Not disclosed')
        for secret in ['PRIVATE_RAW','PRIVATE_EVIDENCE','Excluded grant','-2+3','saved','hidden']:
            self.assertNotIn(secret, payload)
        # Including hidden restores a formula title and a tab-leading cell safely.
        p.locator('.filters summary').click()
        p.select_option('#local', 'all')
        with p.expect_download() as pending:
            p.locator('#download-csv').click()
        all_rows = list(csv.DictReader(io.StringIO(Path(pending.value.path()).read_text(encoding='utf-8-sig'))))
        self.assertEqual(all_rows[1]['Title'], "'-2+3")
        self.assertEqual(all_rows[1]['Organization'], "'\tspreadsheet")
        p.fill('#search','no-such-export')
        self.assertTrue(p.locator('#download-csv').is_disabled())

    def test_copy_filtered_url_and_reload(self):
        p = self.page
        p.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value => {window.copiedLink=value}}})")
        p.locator('#queue-all').click()
        p.fill('#search','Engineering')
        p.select_option('#sort','newest')
        p.locator('#copy-link').click()
        p.wait_for_function('!!window.copiedLink')
        copied = p.evaluate('window.copiedLink')
        self.assertEqual(copied, p.url)
        params = parse_qs(urlsplit(copied).query)
        self.assertEqual(params['search'], ['Engineering'])
        self.assertEqual(params['queue'], ['all'])
        self.assertEqual(params['sort'], ['newest'])
        self.assertIn('Device-only actions are not shared', p.locator('#share-message').inner_text())
        p.goto(copied)
        self.assertEqual(p.locator('.opp-card').count(),1)
        self.assertEqual(p.locator('.opp-card').get_attribute('data-id'),'ui-test-e')

    def test_clipboard_denied_has_selected_keyboard_fallback(self):
        p = self.page
        p.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async () => {throw new Error('Denied')}}})")
        p.fill('#search','Product')
        p.locator('#copy-link').click()
        p.wait_for_selector('#share-url', state='visible')
        self.assertEqual(p.locator('#share-url').input_value(),p.url)
        self.assertEqual(p.evaluate('document.activeElement.id'),'share-url')
        self.assertEqual(p.evaluate("document.querySelector('#share-url').selectionEnd"),len(p.url))
        self.assertIn('Clipboard unavailable',p.locator('#share-message').inner_text())
        p.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})")
        p.locator('#copy-link').click()
        self.assertTrue(p.locator('#share-url').is_visible())

    def test_zero_best_hidden_and_filter_reasons_are_distinct(self):
        p = self.page
        p.locator('#queue-best').click()
        self.assertIn('No verified best matches yet',p.locator('#empty').inner_text())
        p.locator('#queue-review').click()
        p.fill('#search','Product')
        p.get_by_role('button',name='Hide:',exact=False).click()
        self.assertIn('1 matching opportunity is hidden on this device',p.locator('#empty').inner_text())
        p.locator('.filters summary').click()
        p.select_option('#local','all')
        self.assertEqual(p.locator('.opp-card').count(),1)
        p.fill('#search','nonexistent')
        self.assertIn('No opportunities match these filters',p.locator('#empty').inner_text())
        p.goto(self.url+'/?empty=1')
        self.assertIn('No opportunities are loaded',p.locator('#empty').inner_text())

    def test_pay_unknown_is_explicit(self):
        p = self.page
        self.assertEqual(p.locator('.opp-card').filter(has_text='Pay / funding: Not disclosed').count(),2)
        p.goto(self.url+'/additions?export=1')
        self.assertIn('Pay / funding: -42',p.locator('[data-id="formula-1"]').inner_text())

    def test_health_aggregates_actual_failures_age_and_unrun(self):
        p = self.page
        p.add_init_script(f'Date.now = () => Date.parse({json.dumps(NOW)})')
        p.goto(self.url+'/additions')
        summary = p.locator('#health-summary').inner_text()
        for expected in ['1/5 fresh','1 failed','1 stale','1 never succeeded','last success']:
            self.assertIn(expected,summary)
        p.locator('#health-summary').click()
        self.assertEqual(p.locator('.health-good').count(),1)
        self.assertIn('4d ago',p.locator('.health-row').filter(has_text='Old source').inner_text())
        self.assertIn('failed attempt',p.locator('.health-row').filter(has_text='Failed recent source').inner_text())
        self.assertIn('never succeeded',p.locator('.health-row').filter(has_text='Unrun source').inner_text())
        self.assertIn('future timestamp; check clock',p.locator('.health-row').filter(has_text='Future source').inner_text())
        self.assertIn('at least 24h',p.locator('.health-note').inner_text())

    def test_added_tools_remain_mobile_compact(self):
        p = self.page
        for width in [320,390,449]:
            p.set_viewport_size({'width':width,'height':844})
            self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'),width)
            top = p.locator('.opp-card').first.bounding_box()['y']
            print(f'Additions: {width}px first result top: {top}px')
            self.assertLess(top,450)
        p.locator('#download-csv').focus()
        self.assertEqual(p.evaluate('document.activeElement.id'),'download-csv')
        p.keyboard.press('Tab')
        self.assertEqual(p.evaluate('document.activeElement.id'),'copy-link')


if __name__ == '__main__':
    unittest.main(verbosity=2)
