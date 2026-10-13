"""Evolus REST client — 封裝我們 app 驅動 Evolus 的三個呼叫。

官方確認：
- REST base: https://api.evolus.ai/api/v1  (X-ApiKey + X-Application-Id)
- Agent chat:  https://api.evolus.ai/v1    (Authorization: Bearer <app key>, model=<appId>:<agentId>)
- AI Tools:    POST /api/v1/ai-tools/{tool}/run  {text, secondaryText, options}
- Workflow:    POST /api/v1/workflows/{id}/execute (multipart: input/file/callbackUrl)
認證走環境變數，絕不硬編碼。
"""
from __future__ import annotations
import os
import requests

BASE = "https://api.evolus.ai"
REST = f"{BASE}/api/v1"
CHAT = f"{BASE}/v1"


class EvolusClient:
    def __init__(self, api_key=None, app_id=None):
        self.api_key = api_key or os.environ["EVOLUS_API_KEY"]
        self.app_id = app_id or os.environ["EVOLUS_APP_ID"]
        self.rest_h = {"X-ApiKey": self.api_key, "X-Application-Id": self.app_id}
        self.chat_h = {"Authorization": f"Bearer {self.api_key}"}

    # ---- agent chat（OpenAI 兼容，用於讓 agent 解釋策略/寫信）----
    def chat(self, model: str, message: str, conversation_id: str | None = None):
        h = dict(self.chat_h)
        body = {"model": model, "messages": [{"role": "user", "content": message}]}
        if conversation_id:
            h["X-Conversation-Id"] = conversation_id
        r = requests.post(f"{CHAT}/chat/completions", headers=h, json=body, timeout=60)
        r.raise_for_status()
        return r.json()

    def list_models(self):
        r = requests.get(f"{CHAT}/models", headers=self.chat_h, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- AI Tools（email 寫/回覆）----
    def run_email_tool(self, notes: str, reply_to: str | None = None, tone: str = "commercial"):
        body = {"text": notes, "options": {"mode": "write" if not reply_to else "reply", "tone": tone}}
        if reply_to:
            body["secondaryText"] = reply_to
        r = requests.post(f"{REST}/ai-tools/email/run", headers=self.rest_h, json=body, timeout=60)
        r.raise_for_status()
        return r.json().get("output")

    # ---- workflow 觸發（email-in 流程）----
    def execute_workflow(self, workflow_id: str, input_text: str = "", file_path: str | None = None,
                         callback_url: str | None = None):
        files = {}
        if input_text:
            files["input"] = (None, input_text)
        if file_path:
            files["file"] = open(file_path, "rb")
        if callback_url:
            files["callbackUrl"] = (None, callback_url)
        r = requests.post(f"{REST}/workflows/{workflow_id}/execute",
                          headers=self.rest_h, files=files, timeout=60)
        return r.status_code, r.headers.get("X-Workflow-Execution-Id")

    def get_execution(self, workflow_id: str, execution_id: str):
        r = requests.get(f"{REST}/workflows/{workflow_id}/executions/{execution_id}",
                         headers=self.rest_h, timeout=30)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    # 快速自檢：列出可聊的 agent（model = <appId>:<agentId>）
    c = EvolusClient()
    print(c.list_models())
