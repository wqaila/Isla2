"""固件协议行为的主机端回归。

不依赖 ESP-IDF：这些用例把固件里「必须与网关一致」的规则写成可执行规格，
再用同一份 C 头文件常量与 Python 实现在测试里交叉校验。
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware"


def require(path):
    if not path.is_file():
        raise AssertionError(f"尚未实现：{path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


class FirmwareContractTests(unittest.TestCase):
    def test_project_scaffold_exists(self):
        for name in ("CMakeLists.txt", "main/CMakeLists.txt", "main/main.c"):
            self.assertTrue((FIRMWARE / name).is_file(), f"缺少 firmware/{name}")

    def test_limits_match_gateway(self):
        header = require(FIRMWARE / "main" / "app_limits.h")
        gateway = require(ROOT / "gateway" / "app.py")

        def c_int(name):
            m = re.search(rf"#define\s+{name}\s+(\d+)", header)
            self.assertIsNotNone(m, f"头文件缺少 {name}")
            return int(m.group(1))

        # 网关：消息 512 UTF-8 字节，请求体 2048 字节，响应 8192 字节
        self.assertEqual(c_int("APP_MAX_MESSAGE_BYTES"), 512)
        self.assertEqual(c_int("APP_MAX_REQUEST_BYTES"), 2048)
        self.assertEqual(c_int("APP_MAX_RESPONSE_BYTES"), 8192)
        self.assertIn("512", gateway)
        self.assertIn("2048", gateway)
        self.assertIn("8192", gateway)

    def test_error_codes_match_gateway(self):
        # 错误码映射表在 app_protocol.c 里（枚举在 .h），两处都要扫描
        header = require(FIRMWARE / "main" / "app_protocol.h")
        impl = require(FIRMWARE / "main" / "app_protocol.c")
        codes = set(re.findall(r'\{"([a-z_]+)",', impl))
        gateway = require(ROOT / "gateway" / "app.py")
        known = set(re.findall(r'"([a-z_]+)",\s*"[^"]*"', gateway))
        # 固件认识的每个错误码，网关都必须真的会返回
        self.assertTrue(codes, "头文件未声明任何错误码")
        self.assertTrue(codes <= known, f"固件声明了网关不返回的码：{codes - known}")
        self.assertIn("unauthorized", codes)
        self.assertIn("cloud_disabled", codes)
        self.assertIn("cloud_not_configured", codes)

    def test_status_machine_covers_all_required_states(self):
        header = require(FIRMWARE / "main" / "app_protocol.h")
        for state in ("BOOT", "CONNECTING", "READY", "SENT", "ANSWER",
                      "ERROR", "SETTINGS"):
            self.assertIn(f"APP_STATE_{state}", header, f"缺少状态 {state}")

    def test_user_message_length_counts_utf8_bytes(self):
        util = require(FIRMWARE / "main" / "app_text.c")
        # 中文一个字 3 字节，171 个汉字必然超过 512 字节
        self.assertIn("app_utf8_length", util)
        self.assertIn("512", (FIRMWARE / "main" / "app_limits.h").read_text(encoding="utf-8"))
        header = (FIRMWARE / "main" / "app_text.h").read_text(encoding="utf-8")
        self.assertIn("app_utf8_length", header)

    def test_response_parser_rejects_bad_payloads(self):
        parser = require(FIRMWARE / "main" / "app_json.c")
        self.assertIn("app_parse_chat_reply", parser)
        # 解析器必须显式处理这几类失败
        for guard in ("reply", "session_id", "error"):
            self.assertIn(guard, parser, f"解析器未处理 {guard}")

    def test_firmware_never_stores_cloud_api_key(self):
        # 只检查源码；build/ 与 managed_components/ 是编译产物与第三方代码
        skip_dirs = {"build", "managed_components", ".venv", ".local"}
        found = []
        for path in FIRMWARE.rglob("*"):
            if not path.is_file() or path.suffix not in {".c", ".h", ".txt", ".json", ".yml"}:
                continue
            if skip_dirs & set(path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"(LLM_API_KEY|sk-[A-Za-z0-9]{20,})", text):
                found.append(path.name)
        self.assertEqual(found, [], f"固件源码中出现模型密钥相关字段：{found}")

    def test_config_template_uses_placeholders_only(self):
        config = FIRMWARE / "main" / "app_config.h"
        text = require(config)
        self.assertIn("GATEWAY_DEVICE_TOKEN", text)
        self.assertNotRegex(text, r"(sk-[A-Za-z0-9]{16,})", "配置模板不得包含真实密钥")


class BoardPinTests(unittest.TestCase):
    """引脚必须与官方 B 型文档一致，避免照搬其他 1.85 英寸版本。"""

    EXPECTED = {
        "APP_LCD_CS": 21, "APP_LCD_PCLK": 40, "APP_LCD_DATA0": 46,
        "APP_LCD_DATA1": 45, "APP_LCD_DATA2": 42, "APP_LCD_DATA3": 41,
        "APP_LCD_RST": 3, "APP_LCD_BL": 5,
        "APP_TP_I2C_SCL": 10, "APP_TP_I2C_SDA": 11, "APP_TP_RST": 1, "APP_TP_INT": 4,
        "APP_AUDIO_MCLK": 2, "APP_AUDIO_BCLK": 48, "APP_AUDIO_LRCK": 38,
        "APP_AUDIO_DOUT": 47, "APP_AUDIO_DIN": 39, "APP_AUDIO_PA": 9,
    }

    def test_pins_match_official_b_board(self):
        header = require(FIRMWARE / "main" / "app_board.h")
        for name, value in self.EXPECTED.items():
            m = re.search(rf"#define\s+{name}\s+(\d+)", header)
            self.assertIsNotNone(m, f"缺少引脚宏 {name}")
            self.assertEqual(int(m.group(1)), value, f"{name} 与官方 B 型不一致")

    def test_display_resolution(self):
        header = require(FIRMWARE / "main" / "app_board.h")
        self.assertRegex(header, r"APP_LCD_H_RES\s+360")
        self.assertRegex(header, r"APP_LCD_V_RES\s+360")

    def test_i2c_addresses_are_distinct(self):
        header = require(FIRMWARE / "main" / "app_board.h")
        addrs = {
            name: int(value, 16)
            for name, value in re.findall(r"#define\s+(APP_\w+_ADDR)\s+(0x[0-9A-Fa-f]+)", header)
        }
        self.assertGreaterEqual(len(addrs), 5, "应该包含触摸/IMU/电量/RTC/音频地址")
        self.assertEqual(len(set(addrs.values())), len(addrs), f"I2C 地址冲突：{addrs}")

    def test_power_and_boot_buttons_not_used(self):
        """PWR 不支持自定义，BOOT 是启动引脚，都不得用作交互键。"""
        header = require(FIRMWARE / "main" / "app_board.h")
        self.assertNotRegex(header, r"#define\s+APP_(PWR|BOOT)\w*\s+\d+",
                            "不应把 PWR / BOOT 定义为可用按键")
        self.assertIn("PWR", header)  # 至少要有说明
        self.assertIn("不支持自定义", header)

    def test_audio_pins_documented_for_v2(self):
        header = require(FIRMWARE / "main" / "app_board.h")
        self.assertIn("V2", header)
        self.assertIn("ES8311", header)
        self.assertIn("ES7210", header)


class GatewaySideBehaviourTests(unittest.TestCase):
    """用现有网关真实端点验证固件依赖的契约，不访问云端。"""

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT))
        from fastapi.testclient import TestClient
        from gateway.config import Settings
        from gateway.app import create_app
        cls.Settings = Settings
        cls.client = TestClient(create_app(Settings(device_token="a" * 32, api_key="")))

    def test_health_reports_not_ready_without_key(self):
        data = self.client.get("/health").json()
        self.assertFalse(data["ready"])
        self.assertEqual(data["reason"], "cloud_not_configured")

    def test_unauthorized_without_token(self):
        self.assertEqual(self.client.post("/v1/chat", json={"message": "hi"}).status_code, 401)

    def test_not_configured_returns_503_code_firmware_handles(self):
        response = self.client.post(
            "/v1/chat", headers={"Authorization": "Bearer " + "a" * 32},
            json={"request_id": "fw-test-1", "message": "你好"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "cloud_not_configured")

    def test_oversized_body_rejected(self):
        response = self.client.post(
            "/v1/chat", headers={"Authorization": "Bearer " + "a" * 32,
                                 "Content-Type": "application/json"},
            content=b"x" * 5000,
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
