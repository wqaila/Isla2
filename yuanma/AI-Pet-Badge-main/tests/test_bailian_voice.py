"""Offline tests: paid-request boundaries and credential handling, no real API calls."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from bailian_voice import VoiceClient, VoiceError, audio_data


class VoiceTests(unittest.TestCase):
    def client(self, handler):
        client = VoiceClient("sk-TEST-SECRET", "cn-beijing", transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_plan_key_is_rejected_before_network_setup(self):
        with self.assertRaises(VoiceError) as result:
            VoiceClient("sk-sp-TEST-SECRET", "cn-beijing")
        self.assertNotIn("sk-sp-TEST-SECRET", str(result.exception))
        self.assertIn("未发送请求", str(result.exception))

    def test_token_plan_voice_query_uses_matching_endpoint(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"output": {"voice_list": []}})
        client = VoiceClient("sk-sp-TEST-SECRET", "cn-beijing", service="token-plan",
                             transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        client.enroll("list_voice", page_index=0, page_size=10)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "token-plan.cn-beijing.maas.aliyuncs.com")
        self.assertEqual(requests[0].url.path, "/api/v1/services/audio/tts/customization")

    def test_ipv6_loopback_host_cidr_does_not_break_client_setup(self):
        with patch.dict(os.environ, {"NO_PROXY": "localhost,::1/128,127.0.0.1/32",
                                     "HTTPS_PROXY": "http://127.0.0.1:7890"}, clear=True):
            client = VoiceClient("sk-TEST-SECRET", "cn-beijing")
            try:
                self.assertEqual(os.environ["NO_PROXY"], "localhost,::1,127.0.0.1")
                self.assertEqual(os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")
            finally:
                client.close()

    def test_auth_error_does_not_echo_secret_or_raw_response(self):
        def handler(request):
            return httpx.Response(401, json={"code": "InvalidApiKey", "message": "sk-TEST-SECRET"})
        with self.assertRaises(VoiceError) as result:
            self.client(handler).enroll("list_voice")
        self.assertNotIn("sk-TEST-SECRET", str(result.exception))
        self.assertIn("401", str(result.exception))

    def test_missing_enrollment_model_is_not_reported_as_invalid_key(self):
        def handler(request):
            return httpx.Response(404, json={"code": "InvalidParameter", "message": "Model not exist."})
        with self.assertRaises(VoiceError) as result:
            self.client(handler).enroll("list_voice")
        self.assertIn("模型不存在", str(result.exception))
        self.assertIn("不等于 Key 失效", str(result.exception))

    def test_api_redirect_does_not_forward_key(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(307, headers={"Location": "https://unrelated.example/api"})
        with self.assertRaises(VoiceError):
            self.client(handler).enroll("list_voice")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "dashscope.aliyuncs.com")

    def test_create_timeout_does_not_retry(self):
        requests = []
        def handler(request):
            requests.append(request)
            raise httpx.ReadTimeout("timeout", request=request)
        with self.assertRaises(VoiceError):
            self.client(handler).enroll("create_voice", target_model="cosyvoice-v3.5-flash")
        self.assertEqual(len(requests), 1)

    def test_model_mismatch_stops_before_synthesis(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v2"}})
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(VoiceError):
                self.client(handler).speak("voice", "cosyvoice-v3.5-flash", "hello", Path(folder) / "x.wav")
        self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads(requests[0].content)["input"]["action"], "query_voice")

    def test_external_voice_for_other_model_stops_before_any_request(self):
        requests = []
        def handler(request):
            requests.append(request)
            raise AssertionError("No request expected")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(VoiceError):
                self.client(handler).speak_existing("cosyvoice-v2-elysia-test", "qwen-audio-3.0-tts-plus",
                                                   "hello", Path(folder) / "preview.wav")
        self.assertEqual(requests, [])

    def test_existing_token_plan_voice_calls_synthesis_without_registration(self):
        requests = []
        buf = io.BytesIO()
        with wave.open(buf, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 240)
        def handler(request):
            requests.append(request)
            if request.method == "POST":
                self.assertTrue(request.url.path.endswith("SpeechSynthesizer"))
                self.assertEqual(json.loads(request.content)["input"]["voice"], "qwen-audio-3.0-tts-plus-elysia-test")
                return httpx.Response(200, json={"output": {"audio": {"url": "https://audio.example/preview.wav"}}})
            return httpx.Response(200, content=buf.getvalue())
        client = VoiceClient("sk-sp-TEST-SECRET", "cn-beijing", service="token-plan",
                             transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        with tempfile.TemporaryDirectory() as folder:
            client.speak_existing("qwen-audio-3.0-tts-plus-elysia-test", "qwen-audio-3.0-tts-plus",
                                  "hello", Path(folder) / "preview.wav")
        self.assertEqual(len(requests), 2)
        self.assertNotIn("Authorization", requests[1].headers)

    def test_synthesis_download_and_redirect_do_not_carry_api_key(self):
        requests = []
        buf = io.BytesIO()
        with wave.open(buf, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 240)
        wav = buf.getvalue()
        def handler(request):
            requests.append(request)
            if request.url.path.endswith("customization"):
                return httpx.Response(200, json={"output": {"status": "OK", "target_model": "cosyvoice-v3.5-flash"}})
            if request.url.path.endswith("SpeechSynthesizer"):
                return httpx.Response(200, json={"output": {"audio": {"url": "https://audio.example/first.wav"}}})
            if request.url.path.endswith("first.wav"):
                return httpx.Response(302, headers={"Location": "https://storage.example/final.wav"})
            return httpx.Response(200, content=wav)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "preview.wav"
            self.client(handler).speak("voice", "cosyvoice-v3.5-flash", "hello", output)
            self.assertEqual(output.read_bytes(), wav)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all("Authorization" not in r.headers for r in requests[2:]))

    def test_qwen_system_voice_uses_https_and_finalizes_streamed_wav(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 2400)
        data = bytearray(buf.getvalue())
        data[4:8] = (0x7fffffff).to_bytes(4, "little")
        data[40:44] = (0x7fffffff).to_bytes(4, "little")
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"output": {"audio": {
                    "data": "", "url": "http://example.oss-cn-beijing.aliyuncs.com/audio.wav?signature=test"}}})
            return httpx.Response(200, content=bytes(data))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "qwen.wav"
            self.client(handler).synthesize("longanhuan_v3.6", "qwen-audio-3.0-tts-plus", "hello", output)
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnframes(), 2400)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].url.scheme, "https")
        self.assertNotIn("Authorization", requests[1].headers)

    def test_existing_preview_stops_before_cloud_request(self):
        requests = []
        def handler(request):
            requests.append(request)
            raise AssertionError("No request expected")
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "existing.wav"
            output.write_bytes(b"keep")
            with self.assertRaises(VoiceError):
                self.client(handler).speak("voice", "cosyvoice-v3.5-flash", "hello", output)
            self.assertEqual(output.read_bytes(), b"keep")
        self.assertEqual(requests, [])

    def test_pcm_sample_encoded_locally_without_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 16000 * 10)
            self.assertTrue(audio_data(path).startswith("data:audio/wav;base64,"))


if __name__ == "__main__":
    unittest.main()
