import importlib.util
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "soushuba_checkin.py"
SPEC = importlib.util.spec_from_file_location("soushuba_checkin", SCRIPT_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class SouShuBaHelpersTests(unittest.TestCase):
    def test_extract_latest_url_from_publish_page(self):
        page = '''
        <script>
        urls[0]="/url.php?https://example.test/book/?sigin=shu1";
        urls[1]="/url.php?https://example.test/book/?sigin=shu2";
        </script>
        '''
        self.assertEqual(
            "https://publish.test/url.php?https://example.test/book/?sigin=shu1",
            module.extract_latest_url(page, "https://publish.test/sou/go.html"),
        )

    def test_extract_named_latest_link_from_interstitial_page(self):
        page = '<a class="link" href="https://main.test/">最新地址</a>'
        self.assertEqual(
            ["https://main.test/"],
            module.extract_named_links(page, "https://relay.test/"),
        )

    def test_extract_hidden_fields_and_credit(self):
        page = (
            '<input type="hidden" name="formhash" value="abc123">'
            '<ul class="creditl"><li><em>银币：</em>69</li></ul>'
        )
        self.assertEqual("abc123", module.extract_formhash(page))
        self.assertEqual("69", module.extract_credit(page))

    def test_detect_today_record_without_matching_footer_date(self):
        page = (
            '<dd class="ptn xg1"><span class="y">2026-09-20 08:00</span></dd>'
            '<footer>GMT+8, 2026-9-20</footer>'
        )
        self.assertTrue(module.has_today_record(page, datetime(2026, 9, 20, 9, 0)))

    def test_login_page_is_not_authenticated(self):
        self.assertFalse(module.is_logged_in('''<a href="member.php?mod=logging&amp;action=login">登录</a>'''))
        self.assertTrue(module.is_logged_in('''<a href="member.php?mod=logging&amp;action=logout">退出</a>'''))


if __name__ == "__main__":
    unittest.main()
