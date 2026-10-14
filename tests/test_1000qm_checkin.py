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

    def request(self, method, url, **kwargs):
        # 脚本统一经 http_request() 走 session.request()，此处保持同样接口
        self.urls.append(url)
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


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


class HttpRequestRetryTests(unittest.TestCase):
    """http_request()：传输层异常与 5xx 的重试策略。"""

    def test_retries_5xx_on_get_then_succeeds(self):
        session = FakeSession(
            [
                FakeResponse("busy", status_code=503),
                FakeResponse("ok", status_code=200),
            ]
        )
        with patch.object(qm_checkin.time, "sleep", return_value=None):
            response = qm_checkin.http_request("GET", "https://x/", session=session)

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, len(session.urls))

    def test_returns_last_5xx_after_exhausting_retries(self):
        session = FakeSession([FakeResponse("busy", status_code=503)] * 3)
        with patch.object(qm_checkin.time, "sleep", return_value=None):
            response = qm_checkin.http_request(
                "GET", "https://x/", session=session, retries=3
            )

        # 重试耗尽后返回最后一次响应，交由调用方 raise_for_status 按原逻辑报错
        self.assertEqual(503, response.status_code)
        self.assertEqual(3, len(session.urls))

    def test_does_not_retry_5xx_on_post(self):
        session = FakeSession([FakeResponse("busy", status_code=503)])
        with patch.object(qm_checkin.time, "sleep", return_value=None):
            response = qm_checkin.http_request("POST", "https://x/", session=session)

        # 写操作不做 5xx 重试，避免重复提交
        self.assertEqual(503, response.status_code)
        self.assertEqual(1, len(session.urls))

    def test_does_not_retry_4xx(self):
        session = FakeSession([FakeResponse("nope", status_code=403)])
        with patch.object(qm_checkin.time, "sleep", return_value=None):
            response = qm_checkin.http_request("GET", "https://x/", session=session)

        # 4xx（登录失效等）判定逻辑保持不变，不重试
        self.assertEqual(403, response.status_code)
        self.assertEqual(1, len(session.urls))

    def test_retries_transport_error_then_raises(self):
        class DeadSession:
            def __init__(self):
                self.calls = 0

            def request(self, method, url, **kwargs):
                self.calls += 1
                raise qm_checkin.requests.exceptions.ReadTimeout("read timed out")

        session = DeadSession()
        with patch.object(qm_checkin.time, "sleep", return_value=None):
            with self.assertRaises(qm_checkin.requests.exceptions.ReadTimeout):
                qm_checkin.http_request("GET", "https://x/", session=session, retries=2)

        self.assertEqual(2, session.calls)


if __name__ == "__main__":
    unittest.main()
