"""可重复的 API、管理读取与内部对话分层负载场景。"""

import os
from uuid import uuid4

from locust import HttpUser, between, events, task
from locust.env import Environment


class CyberNetizenUser(HttpUser):
    """使用合成文本且不把凭证写入结果的管理后台用户。"""

    wait_time = between(0.2, 1.2)

    def on_start(self) -> None:
        bearer = os.getenv("CNB_PERFORMANCE_BEARER_TOKEN", "").strip()
        self.headers = (
            {"Authorization": f"Bearer {bearer}"} if bearer else {"X-CNB-Development-Role": "admin"}
        )
        self.conversation_id = os.getenv("CNB_PERFORMANCE_CONVERSATION_ID", "").strip()

    @task(4)
    def health(self) -> None:
        self.client.get("/health/live", name="GET /health/live")

    @task(3)
    def observability_dashboard(self) -> None:
        self.client.get(
            "/api/v1/observability/dashboard",
            headers=self.headers,
            name="GET /api/v1/observability/dashboard",
        )

    @task(3)
    def conversation_list(self) -> None:
        self.client.get(
            "/api/v1/chat/conversations?limit=20",
            headers=self.headers,
            name="GET /api/v1/chat/conversations",
        )

    @task(1)
    def synthetic_chat_message(self) -> None:
        if not self.conversation_id:
            return
        self.client.post(
            f"/api/v1/chat/conversations/{self.conversation_id}/messages",
            headers=self.headers,
            name="POST /api/v1/chat/conversations/:id/messages",
            json={
                "client_message_id": str(uuid4()),
                "content": "[性能测试合成消息] 请简短回复确认。",
                "attachment_ids": [],
            },
        )


def enforce_quality_gate(environment: Environment, **_: object) -> None:
    """在 Locust 退出时执行可由环境覆盖的性能质量门。"""
    runner = environment.runner
    if runner is None:
        return
    total = runner.stats.total
    maximum_failure_rate = float(os.getenv("CNB_LOAD_MAX_FAILURE_RATE_PERCENT", "1"))
    maximum_p95_ms = float(os.getenv("CNB_LOAD_MAX_P95_MS", "1000"))
    failure_rate = total.fail_ratio * 100
    p95_ms = total.get_response_time_percentile(0.95) or 0
    if total.num_requests == 0 or failure_rate > maximum_failure_rate or p95_ms > maximum_p95_ms:
        environment.process_exit_code = 1


events.quitting.add_listener(enforce_quality_gate)  # pyright: ignore[reportUnknownMemberType]
