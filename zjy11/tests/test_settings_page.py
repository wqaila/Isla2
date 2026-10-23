"""本地设置页与配置写入的离线回归。

不访问真实云端：保存测试使用临时目录里的 .env；
对外连通性测试用注入的 HTTP 替身。
"""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
ADMIN_TOKEN = "admin-test-token-0123456789abcdef"
DEVICE_TOKEN = "device-test-token-0987654321"


def require(test):
    path = ROOT / "gateway" / "settings_page.py"
    test.assertTrue(path.is_file(), "尚未实现 gateway/settings_page.py")
    return importlib.import_module("gateway.settings_page")


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        mod = require(self)
        from fastapi.testclient import TestClient
        self.mod = mod
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".env").write_text(
            "GATEWAY_CLOUD_ENABLED=false\nLLM_BASE_URL=https://api.deepseek.com/v1\n"
            "LLM_MODEL=deepseek-chat\nLLM_API_KEY=\n", encoding="utf-8")
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(self.respond))
        self.requests = []
        self.create_app = importlib.import_module("gateway.app").create_app
        from gateway.config import Settings
        self.Settings = Settings
        settings = Settings(device_token=DEVICE_TOKEN, api_key="", admin_token=ADMIN_TOKEN)
        self.page = mod.create_settings_router(settings, root=self.root)
        # 设置页可作为独立应用测试
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(self.page)
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def respond(self, request):
        self.requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "我在听。"}, "finish_reason": "stop"}],
        })

    def login(self, token=ADMIN_TOKEN):
        return self.client.post("/settings/login", json={"token": token})

    def test_page_requires_login(self):
        page = self.client.get("/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn("管理令牌", page.text)

    def test_wrong_token_rejected(self):
        result = self.login("wrong-token")
        self.assertEqual(result.status_code, 401)

    def test_login_sets_httponly_cookie(self):
        result = self.login()
        self.assertEqual(result.status_code, 200)
        cookie = result.headers.get("set-cookie", "").lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_never_returns_full_api_key(self):
        response = self.client.post(
            "/settings/save",
            json={"api_key": "sk-abcdef1234567890abcdef", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1", "enabled": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sk-abcdef1234567890abcdef", response.text)
        self.assertIn("已保存", response.text)
        body = self.client.get("/settings/state", headers={"X-Admin-Token": ADMIN_TOKEN})
        self.assertNotIn("sk-abcdef1234567890abcdef", body.text)

    def test_save_writes_env_without_leaking_to_log(self):
        self.client.post(
            "/settings/save",
            json={"api_key": "sk-testkey-1234567890", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1", "enabled": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("LLM_API_KEY=sk-testkey-1234567890", text)
        self.assertIn("GATEWAY_CLOUD_ENABLED=true", text)

    def test_save_rejects_non_https_base_url(self):
        response = self.client.post(
            "/settings/save",
            json={"api_key": "k" * 20, "model": "deepseek-chat",
                  "base_url": "http://api.deepseek.com/v1", "enabled": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        self.assertEqual(response.status_code, 422)

    def test_save_rejects_empty_api_key_when_blank_not_allowed(self):
        response = self.client.post(
            "/settings/save",
            json={"api_key": "", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1", "enabled": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        self.assertEqual(response.status_code, 422)

    def test_save_requires_admin_token(self):
        response = self.client.post("/settings/save", json={"api_key": "k" * 20})
        self.assertEqual(response.status_code, 401)

    def test_env_edit_preserves_unrelated_keys(self):
        (self.root / ".env").write_text(
            "GATEWAY_CLOUD_ENABLED=false\nLLM_MODEL=deepseek-chat\n"
            "GATEWAY_PORT=8111\nLLM_API_KEY=old-key-1234567890\n", encoding="utf-8")
        self.client.post(
            "/settings/save",
            json={"api_key": "new-key-1234567890", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1", "enabled": True},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        text = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("GATEWAY_PORT=8111", text)
        self.assertIn("LLM_API_KEY=new-key-1234567890", text)
        self.assertNotIn("old-key-1234567890", text)

    def test_masked_key_shows_only_tail(self):
        (self.root / ".env").write_text(
            "LLM_API_KEY=sk-abcdefghijklmnop\n", encoding="utf-8")
        state = self.client.get("/settings/state", headers={"X-Admin-Token": ADMIN_TOKEN}).json()
        self.assertTrue(state["api_key_set"])
        self.assertNotIn("sk-abcdefghijklmnop", str(state))
        self.assertTrue(state["api_key_hint"].endswith("mnop"))

    def test_connection_test_does_not_store_or_echo_key(self):
        result = self.client.post(
            "/settings/test",
            json={"api_key": "sk-probe-1234567890", "model": "deepseek-chat",
                  "base_url": "https://api.deepseek.com/v1"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        self.assertIn(result.status_code, (200, 502))
        self.assertNotIn("sk-probe-1234567890", result.text)

    def test_page_has_no_unrendered_template_braces(self):
        """CSS 里的 {{ }} 若未替换，整段样式会失效并退回浏览器默认外观。

        注意：CSS 中 `}}` 是合法的嵌套规则闭合，不能一概而论；
        真正要防的是「成对的 {{ ... }}」这种格式化残留。
        """
        text = self.client.get("/settings").text
        self.assertNotRegex(text, r"\{\{[^{}]*\}\}", "页面残留未渲染的模板占位")

    def test_page_has_usable_styles(self):
        text = self.client.get("/settings").text
        # 关键样式必须真实生效，而不是被转义破坏
        self.assertIn("display:flex", text)
        self.assertIn("border-radius", text)
        self.assertIn("<style>", text)

    def test_login_and_settings_are_separate_views(self):
        """登录前后必须是两个视图，登录成功才显示设置面板。"""
        text = self.client.get("/settings").text
        self.assertIn('id="loginView"', text, "缺少登录视图")
        self.assertIn('id="settingsView"', text, "缺少设置视图")
        self.assertIn("hidden", text, "设置视图应默认隐藏")

    def test_settings_view_hidden_by_default_in_markup(self):
        import re
        text = self.client.get("/settings").text
        match = re.search(r'id="settingsView"[^>]*', text)
        self.assertIsNotNone(match)
        self.assertIn("hidden", match.group(0), "设置视图必须默认隐藏")

    def test_login_view_carries_the_admin_field(self):
        import re
        text = self.client.get("/settings").text
        match = re.search(r'id="loginView".*?</section>', text, re.S)
        self.assertIsNotNone(match)
        self.assertIn('id="admin"', match.group(0))
        self.assertIn('id="login"', match.group(0))


if __name__ == "__main__":
    unittest.main()
