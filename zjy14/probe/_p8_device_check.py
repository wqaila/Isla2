"""真机联调诊断：检查设备链路是否就绪。

本脚本用于**设备接上后**快速定位问题，按链路顺序逐段检查：

  ESP32 固件 ──WS──> zjy13 ws/server.py ──HTTP──> zjy14 /synthesize_stream
     (3)                  (2)                          (1)

检查项：
  1. zjy14 服务是否就绪（端口、引擎、音色）
  2. zjy13 WS 服务是否在跑（端口 + /api/devices 里有没有真机在线）
  3. 串口是否有 ESP32 设备（WSL 侧视角，仅供参考）
  4. 协议契约是否对齐（采样率、帧大小、路径）
  5. zjy13 的 TTS_BASE_URL 是否指向 zjy14
  6. 完整链路模拟：zjy14 流式 → 协议帧 → 模拟设备消费

端口说明（历史踩坑）：
  zjy13 的 **设备接入** WS 服务默认跑在 **8765**；
  50000 是 zjy13 自己那套旧 TTS 服务的端口，两者不是一回事。
  早期本脚本把 [2] 写成检查 50000，导致真机明明在线也报"未启动"。

用法：
  wsl -e bash probe/run_d1.sh probe/_p8_device_check.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

ZJY14_BASE = "http://127.0.0.1:50001"
#: zjy13 设备接入 WS 服务（协议 v1）。可用环境变量覆盖。
ZJY13_DEVICE_PORT = int(os.environ.get("ZJY13_DEVICE_PORT", "8765"))
#: zjy13 旧 TTS 服务端口，仅用于 [5] 判断"是否还指向老引擎"。
ZJY13_LEGACY_TTS_PORT = 50000
ZJY13_DIR = Path("/mnt/d/xiangmudaima/zjy13")


def windows_host_candidates() -> list[str]:
    """WSL 里访问 Windows 侧服务时，127.0.0.1 通常不通。

    WSL2 有独立的网络命名空间，`127.0.0.1` 指的是 WSL 自己；
    Windows 侧的服务要用「默认网关」地址访问（NAT 模式），
    镜像网络模式下才走 127.0.0.1。这里把两种都列出来依次试。
    """
    hosts = ["127.0.0.1"]
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if "via" in parts:
                gw = parts[parts.index("via") + 1]
                if gw not in hosts:
                    hosts.append(gw)
    except Exception:  # noqa: BLE001
        pass
    return hosts


def resolve_zjy13_base() -> str:
    """返回能真正连上 zjy13 设备接入端点的 base URL。

    逐个候选主机试 /api/devices/health，第一个通的即为答案。
    全部不通则回落 127.0.0.1，让后续输出显示失败原因。
    """
    for host in windows_host_candidates():
        base = f"http://{host}:{ZJY13_DEVICE_PORT}"
        code, data = http_get(f"{base}/api/devices/health", timeout=3.0)
        if code == 200 and isinstance(data, dict) and data.get("ok"):
            return base
    return f"http://127.0.0.1:{ZJY13_DEVICE_PORT}"


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict | str]:
    """GET 请求，返回 (状态码, 解析结果)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body[:200]
    except urllib.error.HTTPError as e:
        return e.code, f"HTTPError: {e.reason}"
    except urllib.error.URLError as e:
        return 0, f"连接失败: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def check_port(port: int) -> bool:
    """检查端口是否在监听（通过 /proc/net/tcp 判断，不依赖 ss）。"""
    target = f"{port:04X}"
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        p = Path(f)
        if not p.is_file():
            continue
        for line in p.read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[1]
            if local.endswith(f":{target}") and parts[3] == "0A":  # 0A = LISTEN
                return True
    return False


def check_serial() -> list[str]:
    """检查串口设备（WSL 侧看 /dev/ttyS*、/dev/ttyUSB*、/dev/ttyACM*）。"""
    found = []
    for pat in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyS[0-9]*"):
        found.extend(str(p) for p in Path("/dev").glob(pat.split("/")[-1]))
    return sorted(set(found))


def main() -> int:
    print("=" * 96)
    print(" 真机联调诊断")
    print("=" * 96)

    problems: list[str] = []

    # ---- 1. zjy14 ----
    print("\n[1] zjy14 服务（TTS 引擎）")
    code, data = http_get(f"{ZJY14_BASE}/health")
    if code == 200 and isinstance(data, dict):
        print(f"  [OK] {ZJY14_BASE} 就绪")
        print(f"       引擎={data.get('engine')}  采样率={data.get('sample_rate')}  "
              f"设备={data.get('device')}")
        voices = data.get("voices", [])
        print(f"       音色 {len(voices)} 个: "
              f"{[v.get('voice_id') for v in voices]}")
        diag = data.get("diagnostics", {})
        print(f"       文本前端={diag.get('text_frontend')}  "
              f"精度={diag.get('precision')}  "
              f"compile={diag.get('compile_applied')}")
        if not voices:
            problems.append("zjy14 没有任何可用音色")
    else:
        print(f"  [FAIL] {ZJY14_BASE} 不可用: {data}")
        problems.append("zjy14 服务未启动")

    # ---- 2. zjy13 WS 服务 ----
    print("\n[2] zjy13 WebSocket 服务（设备接入点）")
    device_online = False
    # zjy13 跑在 Windows，本脚本跑在 WSL：先找出真正能连通的地址。
    zjy13_base = resolve_zjy13_base()
    print(f"  连接地址: {zjy13_base}")
    code2, data2 = http_get(f"{zjy13_base}/api/devices", timeout=5.0)
    if code2 == 200 and isinstance(data2, dict):
        print(f"  [OK] 端口 {ZJY13_DEVICE_PORT} 可访问")
        # /api/devices 是 zjy13 的只读监控端点：能读到 clients 就说明
        # 不只是服务起来了，而是真的有板子握过手。
        clients = data2.get("clients", [])
        if clients:
            device_online = True
            print(f"       [OK] 真机在线 {len(clients)} 台")
            for c in clients:
                print(f"            device={c.get('device_id')}  "
                      f"peer={c.get('peer')}  state={c.get('state')}  "
                      f"hb={c.get('last_heartbeat_s')}s  "
                      f"heartbeat_ok={c.get('heartbeat_ok')}")
        else:
            print("       [--] 服务在跑，但没有设备连接")
            print("            板子需连上同一 WiFi，并指向本机 "
                  f"{ZJY13_DEVICE_PORT} 端口")
            problems.append("zjy13 WS 在跑但没有真机连接")
    else:
        print(f"  [--] 连不上 zjy13 设备接入端点: {data2}")
        print("       设备联调前需启动 zjy13 的 WS 服务（Windows 侧）")
        print("       注：WSL2 里 127.0.0.1 是 WSL 自己，要用默认网关地址访问 Windows")
        problems.append("zjy13 WS 服务未启动")

    # ---- 3. 串口 ----
    print("\n[3] 串口设备")
    serials = check_serial()
    if serials:
        print(f"  找到 {len(serials)} 个: {serials[:6]}")
        real = [s for s in serials if "USB" in s or "ACM" in s]
        if real:
            print(f"  [OK] 可能有 ESP32 连接: {real}")
        else:
            print("  [--] 只有内置串口，未见 USB 转串口设备")
            print("       接上开发板后应出现 /dev/ttyUSB0 或 /dev/ttyACM0")
    else:
        print("  [--] 未发现任何串口设备")
    print("       注：WSL 默认不自动挂载 Windows 的 COM 口，")
    print("           需 usbipd 转发，或直接在 Windows 侧烧录固件。")

    # ---- 4. 协议契约 ----
    print("\n[4] 协议契约对齐")
    try:
        from custom_voice.bridge import describe_compat

        d = describe_compat()
        print(f"  协议 v{d['protocol_version']}  magic={d['binary_magic']}  "
              f"帧={d['frame_bytes']}B({d['frame_ms']}ms)")
        print(f"  采样率={d['sample_rate']}  首突发={d['first_burst_ms']}ms  "
              f"固件环缓={d['firmware_ring_ms']}ms")
        print(f"  流路径={d['http_stream_path']}")

        if d["sample_rate"] != 24000:
            problems.append(f"采样率 {d['sample_rate']} 与固件 24000 不一致")
        if d["http_stream_path"] != "/synthesize_stream":
            problems.append("流路径与 zjy13 TTSClient 期望不一致")
        print("  [OK] 契约常量正确")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] 读取契约失败: {e}")
        problems.append("bridge 模块不可用")

    # ---- 5. zjy13 侧配置 ----
    print("\n[5] zjy13 的 TTS 指向")
    env_file = ZJY13_DIR / ".env"
    if env_file.is_file():
        text = env_file.read_text(encoding="utf-8", errors="replace")
        tts_url = ""
        for line in text.splitlines():
            if line.strip().startswith("TTS_BASE_URL"):
                tts_url = line.split("=", 1)[-1].strip()
        if tts_url:
            print(f"  TTS_BASE_URL = {tts_url}")
            if ":50001" in tts_url:
                print("  [OK] 已指向 zjy14")
            elif f":{ZJY13_LEGACY_TTS_PORT}" in tts_url:
                print(f"  [--] 仍指向 zjy13 自己的 TTS 服务"
                      f"（:{ZJY13_LEGACY_TTS_PORT}）")
                print("       切到 zjy14 需改为 :50001，然后重启 zjy13 WS 服务")
                problems.append("zjy13 的 TTS 仍指向旧引擎")
            else:
                print("  [??] 端口不是 50000/50001，请确认")
        else:
            print(f"  [--] .env 里没有 TTS_BASE_URL"
                  f"（使用默认 :{ZJY13_LEGACY_TTS_PORT}，即 zjy13 旧引擎）")
            problems.append("zjy13 未配置 TTS_BASE_URL，板子听到的是旧引擎")
    else:
        print(f"  [--] {env_file} 不存在")

    # ---- 6. 端到端模拟 ----
    print("\n[6] 端到端模拟（zjy14 流式 → 协议帧 → 模拟设备）")
    if code != 200:
        print("  [SKIP] zjy14 未就绪")
    else:
        try:
            from custom_voice.bridge import FakeDevice
            from custom_voice.bridge.protocol import (
                BYTES_PER_SEC,
                DEFAULT_FRAME_BYTES,
                FIRST_BURST_MS,
                encode_binary_frame,
                iter_frames,
            )

            # 直接从 HTTP 流取真实音频
            body = json.dumps({
                "text": "设备联调测试，今天天气不错，我们出去走走吧。",
                "voice_id": "elysia",
            }).encode()
            req = urllib.request.Request(
                f"{ZJY14_BASE}/synthesize_stream", data=body,
                headers={"Content-Type": "application/json"},
            )
            import struct

            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()

            # 解析长度前缀
            pcm_chunks = []
            pos = 0
            while pos + 4 <= len(raw):
                n = struct.unpack(">I", raw[pos:pos + 4])[0]
                pos += 4
                if n == 0:
                    break
                pcm_chunks.append(raw[pos:pos + n])
                pos += n

            total = sum(len(c) for c in pcm_chunks)
            print(f"  HTTP 流: {len(pcm_chunks)} 块  {total} 字节  "
                  f"{total / BYTES_PER_SEC:.2f}s")

            frames, stats = iter_frames(
                pcm_chunks, frame_bytes=DEFAULT_FRAME_BYTES
            )
            min_burst = BYTES_PER_SEC * FIRST_BURST_MS // 1000
            burst_ok = stats.first_frame_bytes >= min_burst or total < min_burst
            print(f"  协议帧: {stats.frames} 帧  首帧 {stats.first_frame_bytes}B "
                  f"({'满足' if burst_ok else '不满足'} {FIRST_BURST_MS}ms 首突发)")

            dev = FakeDevice(ring_ms=200)
            for f in frames:
                dev.feed(f)
            dev.feed(encode_binary_frame(b"", seq=stats.frames))
            dev.drain()
            s = dev.summary()
            print(f"  模拟设备: 收 {s['frames']} 帧  开播={s['started']}  "
                  f"收尾={s['got_end']}  seq错误={len(s['seq_errors'])}")

            if s["seq_errors"]:
                problems.append(f"seq 跳变: {s['seq_errors'][:2]}")
            if not s["got_end"]:
                problems.append("模拟设备未收到结束标记")
            if not burst_ok:
                problems.append("首帧不满足 300ms 首突发要求")

            if not s["seq_errors"] and s["got_end"] and burst_ok:
                print("  [OK] 链路可通")
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] 模拟失败: {type(e).__name__}: {e}")
            problems.append(f"端到端模拟失败: {e}")

    # ---- 汇总 ----
    print()
    print("=" * 96)
    print(" 结论")
    print("=" * 96)
    if problems:
        print(f"  发现 {len(problems)} 个待解决项：")
        for p in problems:
            print(f"    - {p}")
        print()
        print("  真机联调前的准备：")
        print("    1. 启动 zjy14: .\\scripts\\start.ps1 cosyvoice3")
        print(f"    2. 启动 zjy13 WS 服务（设备接入端口 {ZJY13_DEVICE_PORT}）")
        print("    3. 把 zjy13/.env 的 TTS_BASE_URL 改为 :50001 并重启 zjy13")
        print("    4. 板子连上同一 WiFi（固件里 BADGE_VOICE_AUTOCONNECT_HOST 指向本机）")
        return 1

    print("  [OK] 软件链路全部就绪。")
    if device_online:
        print("  [OK] 真机已连接，可以按板子上的按钮跑一轮对话。")
        print("       若板子无声，按以下顺序排查：")
        print("         1. 是否收到 playback_start（需首块音频到达）")
        print("         2. 音量是否过低（串口发 VOL 查/设）")
        print("         3. I2S 是否被录音占用（半双工，不能同时）")
    else:
        print("       接入真机后，若仍无声，按以下顺序排查：")
        print("         1. 板子是否连上 WiFi 并建立 WS 连接（看 zjy13 日志）")
        print("         2. 是否收到 playback_start（需首块音频到达）")
        print("         3. 音量是否过低（固件默认值）")
        print("         4. I2S 是否被录音占用（半双工，不能同时）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
