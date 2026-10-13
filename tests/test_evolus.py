"""Evolus client 單元測試（離線）。不呼叫真實 API；mock requests。

需要 requests；若未裝則跳過（CI 可選）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import requests
except ImportError:
    print("SKIP test_evolus: requests not installed")
    raise SystemExit(0)

import unittest
from unittest import mock
from src.evolus import EvolusClient


class TestEvolusClient(unittest.TestCase):
    def setUp(self):
        self.c = EvolusClient(api_key="k", app_id="a")

    @mock.patch("src.evolus.client.requests.post")
    def test_run_email_tool(self, m_post):
        m_post.return_value = mock.Mock(status_code=200,
                                        json=lambda: {"output": "draft here",
                                                      "creditsConsumed": 1})
        out = self.c.run_email_tool("notes", reply_to="hi")
        self.assertEqual(out, "draft here")
        # 確認 header 有帶 key
        _, kwargs = m_post.call_args
        self.assertEqual(kwargs["headers"]["X-ApiKey"], "k")
        self.assertEqual(kwargs["headers"]["X-Application-Id"], "a")

    @mock.patch("src.evolus.client.requests.post")
    def test_execute_workflow(self, m_post):
        m_post.return_value = mock.Mock(status_code=202, headers={
            "X-Workflow-Execution-Id": "exec-123"})
        code, exec_id = self.c.execute_workflow("wf1", input_text="x")
        self.assertEqual(code, 202)
        self.assertEqual(exec_id, "exec-123")


if __name__ == "__main__":
    unittest.main()
