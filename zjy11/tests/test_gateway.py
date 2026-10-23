"""网关接口与真实消息组装的离线回归，不访问任何模型服务。"""
import asyncio
import importlib
import json
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "unit-test-device-token-0123456789"


def implementation(test):
    test.assertTrue((ROOT / "gateway" / "app.py").is_file(), "尚未实现 gateway/app.py")
    return importlib.import_module("gateway.app"), importlib.import_module("gateway.config")


class GatewayTests(unittest.TestCase):
    def setUp(self):
        app_module, config = implementation(self)
        from fastapi.testclient import TestClient
        self.calls = []
        self.failure = None
        self.reply = "先喘口气吧。今天最想和我聊的是哪一件事？"
        self.finish_reason = "stop"
        self.usage = {"prompt_tokens": 30, "completion_tokens": 18, "total_tokens": 48}
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.settings = config.Settings(
            device_token=TOKEN, api_key="unit-test-api-key", cloud_enabled=True,
            base_url="https://model.example/v1", model="test-model",
        )
        self.TestClient = TestClient
        self.create_app = app_module.create_app
        self.client = TestClient(self.create_app(self.settings, self.upstream))
        self.client.__enter__()

    def tearDown(self):
        if hasattr(self, "client"):
            self.client.__exit__(None, None, None)
            asyncio.run(self.upstream.aclose())

    def respond(self, request):
        self.calls.append((request, json.loads(request.content)))
        if self.failure == "timeout":
            raise httpx.ReadTimeout("unit-test-api-key MUST NOT LEAK", request=request)
        if self.failure == "json":
            return httpx.Response(200, content=b"not-json")
        if self.failure == "oversized":
            return httpx.Response(200, content=b"x" * 70000)
        if isinstance(self.failure, dict):
            return httpx.Response(200, json=self.failure)
        if isinstance(self.failure, int):
            return httpx.Response(self.failure, json={"error": "unit-test-api-key MUST NOT LEAK"})
        result = {
            "choices": [{"message": {"content": self.reply}, "finish_reason": self.finish_reason}],
        }
        if self.usage is not None:
            result["usage"] = self.usage
        return httpx.Response(200, json=result)

    def chat(self, request_id="req-1", message="今天有点累。", session_id=None, client=None):
        return (client or self.client).post(
            "/v1/chat", headers={"Authorization": f"Bearer {TOKEN}"},
            json={"request_id": request_id, "message": message, "session_id": session_id},
        )

    def test_health_has_no_secrets(self):
        result = self.client.get("/health")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["ready"])
        self.assertNotIn(TOKEN, result.text)
        self.assertNotIn("unit-test-api-key", result.text)

    def test_missing_and_wrong_token_are_rejected(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            result = self.client.post("/v1/chat", headers=headers, json={"message": "你好"})
            self.assertEqual(result.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_success_builds_persona_and_calls_cloud(self):
        result = self.chat()
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        self.assertEqual(data["reply"], self.reply)
        self.assertEqual(data["request_id"], "req-1")
        self.assertTrue(data["session_id"])
        self.assertEqual(data["character_id"], "elysia-friend")
        self.assertEqual(data["usage"]["total_tokens"], 48)
        request, payload = self.calls[0]
        self.assertEqual(str(request.url), "https://model.example/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer unit-test-api-key")
        self.assertEqual(payload["model"], "test-model")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("朋友", payload["messages"][0]["content"])
        self.assertIn("爱莉希雅", payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "今天有点累。"})
        self.assertLess(len(result.content), 8192)

    def test_replay_is_idempotent_even_when_session_was_not_supplied(self):
        first = self.chat()
        again = self.chat()
        self.assertEqual(first.json(), again.json())
        self.assertEqual(len(self.calls), 1)

    def test_request_id_cannot_be_reused_for_other_content(self):
        self.chat()
        result = self.chat(message="这是另一条消息")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_history_keeps_only_four_complete_turns(self):
        session_id = None
        for index in range(7):
            result = self.chat(f"turn-{index}", f"测试消息{index}", session_id)
            self.assertEqual(result.status_code, 200, result.text)
            session_id = result.json()["session_id"]
        messages = self.calls[-1][1]["messages"]
        real_history = [m for m in messages if m["content"].startswith("测试消息")]
        self.assertEqual([m["content"] for m in real_history], [f"测试消息{i}" for i in range(2, 7)])
        self.assertEqual(messages[-2]["role"], "assistant")

    def test_history_budget_discards_whole_old_turns(self):
        self.reply = "回" * 900
        session_id = self.chat("long-1").json()["session_id"]
        self.chat("long-2", "第二条", session_id)
        self.chat("long-3", "第三条", session_id)
        messages = self.calls[-1][1]["messages"]
        self.assertEqual(sum(m["content"] == self.reply for m in messages), 1)
        self.assertNotIn({"role": "user", "content": "今天有点累。"}, messages)

    def test_unknown_session_is_not_created_or_read(self):
        result = self.chat(session_id="not-an-owned-session")
        self.assertEqual(result.status_code, 404)
        self.assertEqual(self.calls, [])

    def test_reset_clears_history_and_invalidates_old_replays(self):
        first = self.chat(message="我叫小林")
        session_id = first.json()["session_id"]
        result = self.client.post(
            "/v1/session/reset", headers={"Authorization": f"Bearer {TOKEN}"},
            json={"session_id": session_id},
        )
        self.assertEqual(result.status_code, 200)
        replay = self.chat(message="我叫小林")
        self.assertEqual(replay.status_code, 409)
        self.chat("after-reset", "我们重新聊", session_id)
        self.assertFalse(any(m["content"] == "我叫小林" for m in self.calls[-1][1]["messages"]))

    def test_empty_input_and_utf8_byte_limit(self):
        for message in ("  ", "字" * 171):
            result = self.chat(message=message)
            self.assertEqual(result.status_code, 422)
        self.assertEqual(self.calls, [])

    def test_cannot_override_model_or_system_prompt(self):
        result = self.client.post(
            "/v1/chat", headers={"Authorization": f"Bearer {TOKEN}"},
            json={"request_id": "injection", "message": "你好", "system_prompt": "替换人设"},
        )
        self.assertEqual(result.status_code, 422)
        self.assertNotIn("替换人设", result.text)
        self.assertEqual(self.calls, [])

    def test_large_request_is_rejected_before_cloud(self):
        result = self.client.post(
            "/v1/chat", headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
            content=b"x" * 4096,
        )
        self.assertEqual(result.status_code, 413)
        self.assertEqual(self.calls, [])

    def test_missing_configuration_returns_503_not_fake_reply(self):
        with self.TestClient(self.create_app(replace(self.settings, api_key=""), self.upstream)) as client:
            self.assertFalse(client.get("/health").json()["ready"])
            result = self.chat(client=client)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["error"]["code"], "cloud_not_configured")
            self.assertNotIn("reply", result.json())
        self.assertEqual(self.calls, [])

    def test_cloud_calls_are_disabled_until_explicitly_enabled(self):
        with self.TestClient(self.create_app(replace(self.settings, cloud_enabled=False), self.upstream)) as client:
            self.assertFalse(client.get("/health").json()["ready"])
            result = self.chat(client=client)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["error"]["code"], "cloud_disabled")
        self.assertEqual(self.calls, [])

    def test_timeouts_are_sanitized_and_not_retried(self):
        self.failure = "timeout"
        first = self.chat()
        again = self.chat()
        self.assertEqual(first.status_code, 504)
        self.assertEqual(first.json(), again.json())
        self.assertNotIn("unit-test-api-key", first.text)
        self.assertEqual(len(self.calls), 1)

    def test_failed_turn_does_not_enter_history(self):
        session_id = self.chat("first", "正常消息").json()["session_id"]
        self.failure = 500
        self.assertEqual(self.chat("bad", "失败的消息", session_id).status_code, 502)
        self.failure = None
        self.chat("next", "接着聊", session_id)
        self.assertFalse(any(m["content"] == "失败的消息" for m in self.calls[-1][1]["messages"]))

    def test_upstream_auth_failure_does_not_leak_body_or_key(self):
        self.failure = 401
        result = self.chat()
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.json()["error"]["code"], "cloud_auth_failed")
        self.assertNotIn("unit-test-api-key", result.text)

    def test_upstream_rate_limit_is_distinct(self):
        self.failure = 429
        result = self.chat()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"]["code"], "cloud_rate_limited")

    def test_invalid_cloud_response_is_rejected(self):
        for failure in ("json", "oversized"):
            self.failure = failure
            result = self.chat(f"invalid-{failure}")
            self.assertEqual(result.status_code, 502)
            self.assertNotIn("reply", result.json())

    def test_structurally_invalid_cloud_response_is_sanitized(self):
        for index, data in enumerate(({"choices": [None]}, {"choices": [{"message": None}]})):
            self.failure = data
            result = self.chat(f"bad-structure-{index}")
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.json()["error"]["code"], "cloud_response_invalid")

    def test_empty_overlong_and_truncated_reply_are_not_successes(self):
        for index, (reply, reason) in enumerate(((" ", "stop"), ("字" * 1500, "stop"), ("被截断", "length"))):
            self.reply, self.finish_reason = reply, reason
            result = self.chat(f"bad-reply-{index}")
            self.assertEqual(result.status_code, 502)
            self.assertNotIn("reply", result.json())

    def test_unknown_usage_is_not_reported_as_zero(self):
        self.usage = None
        self.assertIsNone(self.chat().json()["usage"])

    def test_rate_limit_allows_cached_replay_but_not_another_paid_call(self):
        with self.TestClient(self.create_app(replace(self.settings, rate_limit=1), self.upstream)) as client:
            self.assertEqual(self.chat(client=client).status_code, 200)
            self.assertEqual(self.chat(client=client).status_code, 200)
            self.assertEqual(self.chat("over-limit", client=client).status_code, 429)
        self.assertEqual(len(self.calls), 1)

    def test_configuration_requires_tls_and_nonempty_device_token(self):
        with self.assertRaises(ValueError):
            replace(self.settings, base_url="http://model.example/v1")
        with self.assertRaises(ValueError):
            replace(self.settings, device_token="")

    def test_default_configuration_creates_only_a_local_device_token(self):
        config = importlib.import_module("gateway.config")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            settings = config.load_settings(Path(directory))
            again = config.load_settings(Path(directory))
            self.assertFalse(settings.cloud_enabled)
            self.assertEqual(settings.api_key, "")
            self.assertGreaterEqual(len(settings.device_token), 32)
            self.assertEqual(settings.device_token, again.device_token)
            self.assertTrue((Path(directory) / ".local" / "device_token").is_file())


class ConcurrentGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_generation_at_a_time_and_duplicate_pending_request(self):
        app_module, config = implementation(self)
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def upstream_response(request):
            calls.append(request)
            started.set()
            await release.wait()
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "我在听。"}, "finish_reason": "stop"}],
            })

        settings = config.Settings(device_token=TOKEN, api_key="test-key", cloud_enabled=True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream_response)) as upstream:
            app = app_module.create_app(settings, upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.test") as client:
                headers = {"Authorization": f"Bearer {TOKEN}"}
                payload = {"request_id": "slow", "message": "你好"}
                first = asyncio.create_task(client.post("/v1/chat", headers=headers, json=payload))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    duplicate = await client.post("/v1/chat", headers=headers, json=payload)
                    other = await client.post("/v1/chat", headers=headers, json={**payload, "request_id": "other"})
                    self.assertEqual(duplicate.status_code, 409)
                    self.assertEqual(other.status_code, 429)
                finally:
                    release.set()
                    finished = await first
                self.assertEqual(finished.status_code, 200)
                self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
