import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "1000qm_checkin.py"
SPEC = importlib.util.spec_from_file_location("qm_checkin", SCRIPT_PATH)
qm_checkin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qm_checkin)


LOGGED_IN = "<script>var discuz_uid = '123';</script>"


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.responses.pop(0)


class PrestigeTaskTests(unittest.TestCase):
    def test_applies_then_explicitly_draws_reward(self):
        session = FakeSession(
            [
                FakeResponse(
                    LOGGED_IN
                    + '<a href="home.php?id=1&amp;do=apply&amp;mod=task">申请任务</a>'
                ),
                FakeResponse(LOGGED_IN + "任务申请成功"),
                FakeResponse(
                    LOGGED_IN
                    + '<a href="home.php?mod=task&amp;id=1&amp;do=draw">领取奖励</a>'
                ),
                FakeResponse(LOGGED_IN + "任务奖励已领取"),
            ]
        )

        with patch.object(qm_checkin, "authenticated_session", return_value=session):
            status, message = qm_checkin.prestige_task_once("auth=secret")

        self.assertEqual("SUCCESS", status)
        self.assertIn("威望 +1", message)
        self.assertIn("do=apply", session.urls[1])
        self.assertIn("item=doing", session.urls[2])
        self.assertIn("do=draw", session.urls[3])

    def test_reports_already_completed_without_drawing_again(self):
        session = FakeSession(
            [
                FakeResponse(LOGGED_IN + "任务中心"),
                FakeResponse(LOGGED_IN + "进行中的任务为空"),
                FakeResponse(
                    LOGGED_IN
                    + "每日威望红包 2026-09-21 08:10 后可以再次申请"
                ),
            ]
        )

        with patch.object(qm_checkin, "authenticated_session", return_value=session):
            status, message = qm_checkin.prestige_task_once("auth=secret")

        self.assertEqual("ALREADY_TODAY", status)
        self.assertIn("2026-09-21 08:10", message)
        self.assertEqual(3, len(session.urls))

    def test_login_page_without_discuz_uid_is_rejected(self):
        page = '<a href="member.php?mod=logging&amp;action=login">登录</a>'
        self.assertFalse(qm_checkin.is_logged_in(page))

    def test_cookie_is_stored_in_session_cookie_jar(self):
        session = qm_checkin.authenticated_session("sid=abc; token=value=with=equals")
        prepared = session.prepare_request(
            qm_checkin.requests.Request("GET", qm_checkin.TASK_PAGE)
        )
        self.assertIn("sid=abc", prepared.headers["Cookie"])
        self.assertIn("token=value=with=equals", prepared.headers["Cookie"])


if __name__ == "__main__":
    unittest.main()
