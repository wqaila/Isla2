"""Check a local DashScope key, enroll a voice, and synthesize an audition."""
import argparse
import base64
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies
import wave

import httpx
from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "backend" / ".env"
CUSTOMIZATION = "/services/audio/tts/customization"


class VoiceError(Exception):
    pass


def normalize_proxy_bypass():
    # HTTPX 0.28 treats IPv6 /128 bypass entries as invalid URLs. A /128
    # network is exactly one host; normalize only these equivalent entries.
    original = getproxies().get("no", "")
    normalized = []
    for entry in original.split(","):
        entry = entry.strip()
        if "/" in entry and "://" not in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
                if network.num_addresses == 1:
                    entry = str(network.network_address)
            except ValueError:
                pass
        normalized.append(entry)
    updated = ",".join(normalized)
    if updated != original:
        # This process only; preserve actual proxy and certificate settings.
        os.environ["NO_PROXY"] = updated


class VoiceClient:
    def __init__(self, key, region, workspace="", transport=None, service="standard"):
        if not key or not key.strip():
            raise VoiceError("请先在 backend/.env 填写 DASHSCOPE_API_KEY；密钥不要发到聊天里。")
        if service not in ("standard", "token-plan"):
            raise VoiceError("DASHSCOPE_SERVICE 应为 standard 或 token-plan。")
        if service == "standard" and key.strip().startswith("sk-sp-"):
            raise VoiceError("套餐 Key 需要匹配套餐入口，不能直接用于通用接口；若已确认是 Token Plan，可指定 --service token-plan。未发送请求。")
        if region not in ("cn-beijing", "ap-southeast-1"):
            raise VoiceError("地域应为 cn-beijing 或 ap-southeast-1，请向老师确认。")
        if workspace and not re.fullmatch(r"[A-Za-z0-9-]+", workspace):
            raise VoiceError("DASHSCOPE_WORKSPACE_ID 格式不正确。")
        if service == "token-plan":
            if region != "cn-beijing" or workspace:
                raise VoiceError("Token Plan 使用北京套餐专属域名，请勿混入其他地域或业务空间 ID。")
            host = "token-plan.cn-beijing.maas.aliyuncs.com"
        elif workspace:
            host = f"{workspace}.{region}.maas.aliyuncs.com"
        else:
            host = "dashscope.aliyuncs.com" if region == "cn-beijing" else "dashscope-intl.aliyuncs.com"
        self.base = f"https://{host}/api/v1"
        self.key = key.strip()
        if transport is None:
            normalize_proxy_bypass()
        try:
            self.http = httpx.Client(timeout=60, transport=transport, follow_redirects=False)
        except (httpx.InvalidURL, ValueError):
            raise VoiceError("本机代理配置格式不兼容，请检查代理及绕过列表；请求尚未发送。") from None

    def close(self):
        self.http.close()

    def call(self, path, payload):
        # No automatic retries: repeating create_voice could create extra voices.
        try:
            response = self.http.post(self.base + path, json=payload,
                                      headers={"Authorization": f"Bearer {self.key}"})
        except httpx.RequestError:
            raise VoiceError("无法连接百炼，请检查网络；本次不自动重试。") from None
        try:
            data = response.json()
        except ValueError:
            raise VoiceError(f"接口未返回 JSON（HTTP {response.status_code}），请确认接口地址。") from None
        if not isinstance(data, dict):
            raise VoiceError("接口返回结构异常。")
        if not response.is_success or data.get("code"):
            # Deliberately omit raw response/message: a provider may echo credentials or audio.
            code = str(data.get("code", "Unknown")).replace(self.key, "REDACTED")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
                code = "Unknown"
            hint = {
                401: "可能是 Key、地域不匹配，或该 Key 来自学校/第三方接口。",
                403: "请老师检查业务空间权限、模型授权及服务开通状态。",
                429: "触发限流或额度限制，请稍后检查。",
            }.get(response.status_code, "请老师结合地域、模型权限及账户状态检查。")
            if response.status_code == 404 and "model not exist" in str(data.get("message", "")).lower():
                hint = "当前入口返回模型不存在，请确认音色注册模型的支持范围；这不等于 Key 失效。"
            raise VoiceError(f"调用失败：HTTP {response.status_code}，code={code}。{hint}")
        output = data.get("output")
        if not isinstance(output, dict):
            raise VoiceError("接口未返回 output，尚不能确认服务可用。")
        return output

    def enroll(self, action, **values):
        return self.call(CUSTOMIZATION, {"model": "voice-enrollment", "input": {"action": action, **values}})

    def ready(self, voice_id, model, wait=False):
        for attempt in range(30 if wait else 1):
            info = self.enroll("query_voice", voice_id=voice_id)
            if info.get("target_model") != model:
                raise VoiceError("音色绑定模型与 TTS_MODEL 不一致，请按创建记录修正配置。")
            status = info.get("status")
            if status == "OK":
                return info
            if status != "DEPLOYING":
                raise VoiceError("音色尚不可用或审核未通过，请查询音色状态。")
            if wait and attempt < 29:
                time.sleep(1)
        raise VoiceError("音色仍在处理中；已保留音色 ID，请稍后运行 status，不要重复创建。")

    def speak(self, voice_id, model, text, output):
        if output.exists():
            raise VoiceError("输出文件已存在，请指定新的 --output，避免重复合成。")
        self.ready(voice_id, model)
        self.synthesize(voice_id, model, text, output)

    def speak_existing(self, voice_id, model, text, output):
        # Token Plan may expose synthesis without voice-enrollment. A supplied
        # existing ID must identify this model; the cloud still checks ownership.
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", voice_id) or not voice_id.startswith(model + "-"):
            raise VoiceError("已有音色 ID 应属于当前 TTS_MODEL；请提供该模型创建的完整 ID。")
        self.synthesize(voice_id, model, text, output)

    def synthesize(self, voice_id, model, text, output):
        if output.exists():
            raise VoiceError("输出文件已存在，请指定新的 --output，避免重复合成。")
        result = self.call("/services/audio/tts/SpeechSynthesizer", {
            "model": model, "input": {"voice": voice_id, "text": text, "format": "wav", "sample_rate": 24000},
        })
        url = result.get("audio", {}).get("url")
        if isinstance(url, str):
            parts = urlsplit(url)
            # The API can return signed OSS URLs using http. Upgrade the scheme
            # for the same Alibaba host/path/query instead of downloading in cleartext.
            if parts.scheme == "http" and parts.hostname and parts.hostname.endswith(".aliyuncs.com"):
                url = urlunsplit(parts._replace(scheme="https"))
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise VoiceError("合成未返回有效的 HTTPS 音频下载地址。")
        try:
            # No Authorization header is sent to the audio storage URL.
            audio = self.http.get(url, follow_redirects=True)
            audio.raise_for_status()
        except httpx.HTTPError:
            raise VoiceError("合成已提交，但下载失败；请检查网络。") from None
        if not audio.content.startswith(b"RIFF") or audio.content[8:12] != b"WAVE":
            raise VoiceError("下载内容不是 WAV，未写入试听文件。")
        try:
            with wave.open(io.BytesIO(audio.content), "rb") as source:
                params = source.getparams()
                pcm = source.readframes(source.getnframes())
            if not pcm or params.nchannels not in (1, 2) or params.sampwidth != 2:
                raise VoiceError("下载的 WAV 缺少有效的 16-bit PCM 数据。")
        except (wave.Error, EOFError):
            raise VoiceError("下载的 WAV 结构异常，未写入试听文件。") from None
        output.parent.mkdir(parents=True, exist_ok=True)
        # Finalize streaming WAV size placeholders so duration is correct in players.
        with output.open("xb") as target, wave.open(target, "wb") as writer:
            writer.setparams(params)
            writer.writeframes(pcm)


def audio_data(path):
    mime = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4"}.get(path.suffix.lower())
    if not mime or not path.is_file():
        raise VoiceError("请选择本地 WAV、MP3 或 M4A 人声样本。")
    if not 0 < path.stat().st_size <= 10_000_000:
        raise VoiceError("声音样本应大于 0 且不超过 10MB。")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                duration = audio.getnframes() / audio.getframerate()
                if not (5 <= duration <= 60 and audio.getframerate() >= 16000 and
                        audio.getsampwidth() == 2 and audio.getnchannels() in (1, 2)):
                    raise VoiceError("WAV 需要 16-bit、至少 16kHz、单/双声道、5～60 秒；建议 10～20 秒。")
        except (wave.Error, EOFError):
            raise VoiceError("无法读取 WAV，请导出为标准 16-bit PCM WAV。") from None
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def save_voice(voice_id, model, region, service="standard"):
    # Keep future commands on the same model/region, including --region overrides.
    set_key(ENV_FILE, "TTS_VOICE_ID", voice_id)
    set_key(ENV_FILE, "TTS_MODEL", model)
    set_key(ENV_FILE, "DASHSCOPE_REGION", region)
    set_key(ENV_FILE, "DASHSCOPE_SERVICE", service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=("cn-beijing", "ap-southeast-1"))
    parser.add_argument("--service", choices=("standard", "token-plan"),
                        help="通用接口或 Token Plan；套餐模式用于 Agent 内素材验证")
    parser.add_argument("--model", help="覆盖本次目标模型，不改变本地 Key")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="仅查询音色列表，不创建音色或合成语音")
    listing = commands.add_parser("list", help="按前缀查询已创建的音色，恢复创建记录")
    listing.add_argument("--prefix", default="elysia")
    clone = commands.add_parser("clone", help="上传本地样本并创建音色")
    clone.add_argument("audio", type=Path)
    clone.add_argument("--prefix", default="elysia")
    status = commands.add_parser("status", help="查询状态，成功后保存音色 ID 到 .env")
    status.add_argument("--voice-id")
    speak = commands.add_parser("speak", help="用配置中的音色合成一段 WAV 试听")
    speak.add_argument("--text", required=True)
    speak.add_argument("--voice-id", help="同账号已创建的音色 ID；Token Plan 由合成接口验证，不查询注册接口")
    speak.add_argument("--output", type=Path, default=ROOT / "artifacts" / "voice" / "preview.wav")
    system = commands.add_parser("system-test", help="用系统音色测试语音合成，不创建复刻音色")
    system.add_argument("--voice", help="系统音色；千问 3.0 Plus 默认 longanhuan_v3.6")
    system.add_argument("--text", default="你好，今天也要开开心心的。")
    system.add_argument("--output", type=Path, default=ROOT / "artifacts" / "voice" / "system-preview.wav")
    args = parser.parse_args()
    load_dotenv(ENV_FILE, override=False)
    region = args.region or os.getenv("DASHSCOPE_REGION", "cn-beijing")
    service = args.service or os.getenv("DASHSCOPE_SERVICE", "standard")
    model = (args.model or os.getenv("TTS_MODEL", "cosyvoice-v3.5-flash")).strip()
    if not (model.startswith("cosyvoice-") or model.startswith("qwen-audio-")) or not re.fullmatch(r"[A-Za-z0-9_.-]+", model):
        raise VoiceError("此工具用于 CosyVoice/Qwen-Audio-TTS，请检查 TTS_MODEL。")
    if model.startswith("qwen-audio-") and "-tts-" not in model:
        raise VoiceError("请选择 Qwen-Audio-TTS 模型，不要使用 ASR 或实时对话模型。")
    client = VoiceClient(os.getenv("DASHSCOPE_API_KEY", ""), region,
                         os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip(), service=service)
    try:
        print(f"接口：{service}；地域：{region}；目标模型：{model}")
        if args.command == "check":
            info = client.enroll("list_voice", page_index=0, page_size=10)
            if not isinstance(info.get("voice_list"), list):
                raise VoiceError("未返回音色列表，尚不能确认接口可用。")
            print(f"音色列表查询成功，本页 {len(info['voice_list'])} 个音色；未创建、删除或合成。")
            print("此结果只验证列表接口；目标模型的创建与合成权限还需实际调用确认。")
        elif args.command == "list":
            if not re.fullmatch(r"[A-Za-z0-9]{1,10}", args.prefix):
                raise VoiceError("prefix 只能包含 1～10 个英文字母或数字。")
            info = client.enroll("list_voice", prefix=args.prefix, page_index=0, page_size=100)
            voices = info.get("voice_list")
            if not isinstance(voices, list):
                raise VoiceError("未返回音色列表。")
            for voice in voices:
                print(f"{voice.get('voice_id', '')}  {voice.get('status', '')}")
            print(f"本页 {len(voices)} 个音色。")
        elif args.command == "clone":
            if not re.fullmatch(r"[A-Za-z0-9]{1,10}", args.prefix):
                raise VoiceError("prefix 只能包含 1～10 个英文字母或数字。")
            info = client.enroll("create_voice", target_model=model, prefix=args.prefix,
                                 audio=audio_data(args.audio))
            voice_id = info.get("voice_id")
            if not isinstance(voice_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", voice_id):
                raise VoiceError("创建响应未包含有效音色 ID，请查询列表，避免重复创建。")
            record = ROOT / "artifacts" / "voice" / f"created-{time.time_ns()}.json"
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(json.dumps({"voice_id": voice_id, "target_model": model, "region": region,
                                          "service": service, "endpoint": client.base}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已创建音色：{voice_id}；创建记录：{record}")
            print("如果后续查询失败，使用该 ID 运行 status；不要重复运行 clone。")
            client.ready(voice_id, model, wait=True)
            save_voice(voice_id, model, region, service)
            print("音色已就绪，音色 ID、模型和地域已写入本地 backend/.env。")
        elif args.command == "status":
            voice_id = args.voice_id or os.getenv("TTS_VOICE_ID", "").strip()
            if not voice_id:
                raise VoiceError("请用 --voice-id 指定创建记录中的音色 ID。")
            client.ready(voice_id, model)
            save_voice(voice_id, model, region, service)
            print("音色已就绪，音色 ID、模型和地域已保存。")
        elif args.command == "system-test":
            if not args.text.strip() or len(args.text) > 500:
                raise VoiceError("试听文字需要 1～500 个字符。")
            voice = args.voice or {"qwen-audio-3.0-tts-plus": "longanhuan_v3.6",
                                   "cosyvoice-v3-flash": "longanyang"}.get(model)
            if not voice:
                raise VoiceError("此模型没有配置默认系统音色，请用 --voice 指定该模型支持的系统音色。")
            client.synthesize(voice, model, args.text, args.output)
            print(f"系统音色试听已保存：{args.output.resolve()}；本次没有创建复刻音色。")
        else:
            voice_id = (args.voice_id or os.getenv("TTS_VOICE_ID", "")).strip()
            if not voice_id:
                raise VoiceError("请先创建音色，或在 backend/.env 填写 TTS_VOICE_ID。")
            if not args.text.strip() or len(args.text) > 500:
                raise VoiceError("试听文字需要 1～500 个字符。")
            if service == "token-plan":
                client.speak_existing(voice_id, model, args.text, args.output)
            else:
                client.speak(voice_id, model, args.text, args.output)
            if args.voice_id:
                # Persist only after the cloud accepted this voice and audio downloaded.
                save_voice(voice_id, model, region, service)
            print(f"试听已保存：{args.output.resolve()}")
    finally:
        client.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except (VoiceError, OSError) as exc:
        print(f"未完成：{exc}", file=sys.stderr)
        sys.exit(1)
