"""Browser regression checks; synthetic fixtures, never production opportunity data.
Run: uv run --with flask --with playwright python test_ui.py
Uses installed Chrome or Edge; does not request scans or change server/database data.
"""
import threading
import unittest
from pathlib import Path
from flask import Flask, render_template, request
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
FIXTURES = [
    dict(id='ui-test-a', title='<img src=x onerror=alert(1)> Product test', organization='Test fixture', queue='review', track_group='product', eligibility_state='unknown', availability_state='unknown', verification_state='conflicting', fit_score=10, evidence=[dict(field='eligibility', quote='<script>unsafe</script>', url='javascript:alert(1)')], source_url='https://example.com/listing?signature=preserve%2Bme', action_label='Verify opening'),
    dict(id='ui-test-b', title='Pilot test', queue='review', track_group='other', kind='pilot', fit_score=20, deadline_at='2027-05-01T12:00:00Z', last_seen_at='2026-10-02T00:00:00Z'),
    dict(id='ui-test-c', title='Accelerator test', queue='resources', track_group='funding', kind='accelerator', fit_score=30, deadline_at='2027-03-01T00:00:00Z'),
    dict(id='ui-test-d', title='Grant test', queue='watch', track_group='funding', kind='grant', fit_score=40),
    dict(id='ui-test-e', title='Engineering test', queue='explore', track_group='engineering', fit_score=50),
]

@app.route('/')
def index():
    rows = [] if request.args.get('empty') else FIXTURES
    return render_template('index.html', opportunities=rows, scanner_health=[dict(source='fixture-source', status='error', error='fixture failure', count=0, last_success='2026-10-01T12:00:00Z', last_attempt='2026-10-02T12:00:00Z')], scan_enabled=bool(request.args.get('enabled')))

class RadarUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server('127.0.0.1', 0, app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.pw = sync_playwright().start()
        errors = []
        for channel in ['chrome', 'msedge', None]:
            try:
                cls.browser = cls.pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception as exc:
                errors.append(str(exc))
        else:
            raise RuntimeError('\n'.join(errors))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width':390,'height':844})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)
        self.page.wait_for_selector('.opp-card')

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.context.close()

    def test_mobile_safe_default_health_and_evidence(self):
        p = self.page
        self.assertEqual(p.locator('#queue-review').get_attribute('aria-pressed'), 'true')
        self.assertEqual(p.locator('.opp-card').count(), 2)
        top = p.locator('.opp-card').first.bounding_box()['y']
        print(f'390px first result top: {top}px')
        self.assertLess(top,450)
        self.assertEqual(p.locator('#scan').count(),0)
        self.assertIn('last success',p.locator('#health-summary').inner_text())
        self.assertNotIn('active',p.locator('#health-summary').inner_text().lower())
        p.locator('#queue-all').click()
        self.assertEqual(p.locator('img, main script').count(),0)
        card = p.locator('[data-id="ui-test-a"]')
        card.locator('summary').click()
        self.assertIn('<script>unsafe</script>',card.inner_text())
        self.assertEqual(p.locator('a[href^="javascript:"]').count(),0)
        self.assertIn('signature=preserve%2Bme',card.locator('a.primary').get_attribute('href'))
        self.assertNotIn('[object Object]',p.locator('body').inner_text())

    def test_filters_counts_url_and_sort(self):
        p=self.page
        p.locator('#queue-all').click()
        p.locator('.filters summary').click()
        p.select_option('#track','funding')
        self.assertEqual(p.locator('.opp-card').count(),3)
        self.assertIn('3 opportunities',p.locator('#result-count').inner_text())
        self.assertIn('track=funding',p.url)
        p.select_option('#sort','deadline')
        self.assertEqual(p.locator('.opp-card').first.get_attribute('data-id'),'ui-test-c')
        p.reload()
        self.assertEqual(p.locator('.opp-card').count(),3)
        p.locator('#clear').click()
        p.locator('.filters summary').click()
        p.select_option('#track','engineering')
        self.assertEqual(p.locator('.opp-card').count(),1)
        self.assertIn('Engineering test',p.locator('main').inner_text())

    def test_device_actions_persist_and_hidden_can_restore(self):
        p=self.page
        card=p.locator('[data-id="ui-test-a"]')
        card.get_by_role('button',name='Save:',exact=False).click()
        p.reload()
        card=p.locator('[data-id="ui-test-a"]')
        self.assertEqual(card.get_by_role('button',name='Saved:',exact=False).get_attribute('aria-pressed'),'true')
        card.get_by_role('button',name='Applied:',exact=False).click()
        card.get_by_role('button',name='Hide:',exact=False).click()
        self.assertEqual(p.locator('.opp-card').count(),1)
        p.locator('.filters summary').click()
        p.select_option('#local','hidden')
        self.assertEqual(p.locator('.opp-card').count(),1)
        p.get_by_role('button',name='Unhide:',exact=False).click()
        self.assertEqual(p.locator('.opp-card').count(),0)
        self.assertIn('device-only',p.locator('#storage-note').inner_text())

    def test_empty_and_scan_failure(self):
        p = self.page
        p.goto(self.url + '/?empty=1')
        self.assertTrue(p.locator('#empty').is_visible())
        self.assertIn('0 opportunities', p.locator('#result-count').inner_text())
        p.goto(self.url + '/?enabled=1')
        p.route('**/api/scan', lambda route: route.fulfill(status=403, content_type='application/json', body='{}'))
        p.locator('#scan').click()
        p.wait_for_function("document.querySelector('#scan-message').textContent.includes('403')")
        self.assertTrue(p.locator('#scan').is_enabled())

    def test_storage_unavailable(self):
        p = self.page
        p.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('disabled')}})")
        p.reload()
        p.locator('[data-id="ui-test-a"]').get_by_role('button', name='Save:', exact=False).click()
        self.assertIn('page only', p.locator('#storage-note').inner_text())

    def test_breakpoints_and_keyboard(self):
        p=self.page
        for width in [320,390,768,1024,1440]:
            p.set_viewport_size({'width':width,'height':844})
            self.assertLessEqual(p.evaluate('document.documentElement.scrollWidth'),width)
        p.keyboard.press('Tab')
        self.assertEqual(p.evaluate('document.activeElement.textContent'),'Skip to opportunities')

if __name__ == '__main__':
    unittest.main(verbosity=2)
