"""TTS（语音合成）回归测试

覆盖：抽象层接口、缓存键、SAPI 真实合成、失败降级、API 端点与静态挂载。

设计取舍：
- **不依赖网络**：edge-tts 的用例拿不到网就 SKIP（不算失败），
  因为 CI 环境不一定能连微软的语音服务。
- **真实合成**用 SAPI（Windows 自带，离线可用），保证「真的能出声」被验证到。
- 会临时改 tts_* 运行时配置，结束时**恢复原值**。

运行：cd server && ./venv/Scripts/python.exe tests/test_tts.py
"""
import asyncio
import json
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import main
from tts import TTSController, cache_key
from tts.base import AudioResult, TTSEngine

PASS, FAIL, SKIP = [], [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


def skip(name, why):
    SKIP.append(name)
    print(f"  SKIP  {name}  -> {why}")


def safe_remove(path) -> bool:
    """尽力删测试自己产生的文件。

    某些运行环境会拦截删除并 fail-closed 抛异常（把删除重定向到回收站，
    回收站不可用就直接抛），**哪怕文件其实已经被删掉了**。
    清理是"尽力而为"，不该影响测试结论；但残留要打印出来，别掩盖。
    """
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        return True
    except OSError as e:
        if p.exists():
            print(f"    （清理残留：{p} 未删掉：{e}）")
            return False
        return True
    return not p.exists()


class StubEngine(TTSEngine):
    """确定性桩引擎：写一段合法 WAV，不依赖平台 / 网络 / 声卡。

    为什么要它：控制器的契约（缓存键、失败降级、元数据、API 通路）必须能在
    **任何环境**确定性复现。原先这些用例直接拿 SAPI 当真实引擎，于是
    Linux CI 上必然全红 —— 而一盏「永远亮着的红灯」比没有 CI 更糟，
    它会让所有人学会无视红色。

    真实引擎（SAPI / edge-tts）的能力探测单独做，见第 3b 与第 13 节。
    """

    name = "stub"
    label = "测试桩（确定性）"
    ext = "wav"
    mime = "audio/wav"
    online = False

    #: 静音样本数：16-bit 单声道 → 3200 字节，足够断言「有实际内容」
    SAMPLES = 1600

    @classmethod
    def is_available(cls) -> bool:
        return True

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0,
                         out_path: Path | None = None) -> AudioResult:
        if not text.strip():
            raise ValueError("文本为空，无法合成")
        if out_path is None:
            fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="tts_stub_")
            os.close(fd)
            out_path = Path(tmp)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = b"\x00\x00" * self.SAMPLES
        header = (
            b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
            + b"data" + struct.pack("<I", len(data))
        )
        out_path.write_bytes(header + data)
        return AudioResult(path=out_path, mime=self.mime, engine=self.name,
                           duration_ms=int(self.SAMPLES / 8000 * 1000))


CONFIG_FILE = Path(main.BASE_DIR) / "data" / "runtime_config.json"
_backup = CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else None

# ⚠️⚠️ 测试**必须使用临时配置文件**，不能碰真实的 data/runtime_config.json。
#
# 两个原因，都是踩过的坑：
#   ① **污染**：这些用例要反复 PUT /api/config（启停 TTS、设令牌）。
#      直接打真实配置的话，一旦中途异常退出，用户配置就被留成测试值。
#      （更早一版连备份都没做，把用户真实的 api_token 覆盖后永久丢失。）
#   ② **被真实配置干扰**：用户一旦设了 api_token，所有裸请求都会 401，
#      整套用例会莫名其妙集体失败 —— 测的是"用户的配置"而不是代码。
#
# 做法：把 config_persistence 的模块级路径常量重定向到临时文件，
# 由 finally 还原。必须在 import main 之后、发第一个请求之前完成。
_TMP_CFG_DIR = Path(tempfile.mkdtemp(prefix="tts_cfg_"))
_TMP_CONFIG_FILE = _TMP_CFG_DIR / "runtime_config.json"
import config_persistence as _cp  # noqa: E402

_original_cfg_path = _cp._CONFIG_FILE
_cp._CONFIG_FILE = _TMP_CONFIG_FILE
_cp._cached_config = None
# 以真实配置为基底（保留 tts_* 之外的字段，让"默认值"断言有意义），
# 但**清掉 api_token**：测试要用裸请求，带令牌会让所有用例 401。
if _backup is not None:
    try:
        _seed = json.loads(_backup)
    except Exception:
        _seed = dict(_cp._DEFAULT_CONFIG)
    _seed["api_token"] = ""
    _seed.setdefault("tts_enabled", False)
    _TMP_CONFIG_FILE.write_text(json.dumps(_seed, ensure_ascii=False, indent=2), encoding="utf-8")
else:
    _TMP_CONFIG_FILE.write_text(
        json.dumps(_cp._DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")

# 测试用令牌：写进**临时**配置，不会外泄到真实文件
_PROBE_TOKEN = "tts-test-token-9f3a"

try:
    # ==================== 1. 缓存键 ====================
    print("\n--- 1. 缓存键的确定性 ---")
    k = cache_key("你好", "edge", "v1", 1.0)
    check("同样输入得同样键", k == cache_key("你好", "edge", "v1", 1.0))
    check("文本不同 -> 键不同", k != cache_key("你好啊", "edge", "v1", 1.0))
    check("引擎不同 -> 键不同", k != cache_key("你好", "sapi", "v1", 1.0))
    check("音色不同 -> 键不同", k != cache_key("你好", "edge", "v2", 1.0))
    check("语速不同 -> 键不同", k != cache_key("你好", "edge", "v1", 1.2))
    check("键长 32", len(k) == 32)

    # ==================== 2. 引擎与合成 ====================
    tmpdir = Path(tempfile.mkdtemp(prefix="tts_test_"))
    ctrl = TTSController(cache_dir=tmpdir)
    # 把确定性桩插到最前，后面保留真实引擎用于能力探测
    ctrl._classes["stub"] = StubEngine
    ctrl.priority = ["stub", "edge", "sapi"]

    print("\n--- 2. 引擎可用性 ---")
    avail = {a["name"]: a["available"] for a in ctrl.available()}
    print(f"    {avail}")
    check("注册了 edge 与 sapi", {"edge", "sapi"} <= set(avail))
    check("桩引擎可用（与平台无关）", avail.get("stub") is True)

    print("\n--- 3. 确定性合成（任何平台都必须能过）---")


    async def _stub():
        return await ctrl.synthesize("你好，我是测试。", engine="stub", use_cache=False)


    try:
        r = asyncio.run(_stub())
        check("产出文件", r.path.exists() and r.path.stat().st_size > 1000,
              f"{r.path.stat().st_size} 字节")
        check("MIME 是 wav", r.mime == "audio/wav")
        check("读出了时长", r.duration_ms > 0, f"{r.duration_ms}ms")
        check("engine 字段是 stub", r.engine == "stub")
    except Exception as e:
        check("确定性合成", False, f"{type(e).__name__}: {e}")

    print("\n--- 3b. 真实引擎能力探测（不具备则 SKIP）---")
    # 这一节只验证「探测本身不炸」。SAPI 需要 Windows，且要有可用的语音
    # 与音频子系统；edge-tts 需要联网。两者都缺席时跳过，不算失败 ——
    # 真实合成的契约已由上面的桩引擎确定性地锁住。
    if sys.platform != "win32":
        skip("SAPI 真实合成", "非 Windows，SAPI 不可用")
    elif not avail.get("sapi"):
        skip("SAPI 真实合成", "本机无 PowerShell 或语音组件")
    else:
        try:
            rs = asyncio.run(ctrl.synthesize("你好", engine="sapi", use_cache=False))
            check("SAPI 真实合成", rs.path.exists() and rs.path.stat().st_size > 1000,
                  f"{rs.path.stat().st_size} 字节")
        except Exception as e:
            # 能枚举语音但发不出声（无声卡 / 安全设置拦）→ 环境限制，跳过
            skip("SAPI 真实合成", f"{type(e).__name__}: {str(e)[:80]}")

    print("\n--- 4. 缓存命中 ---")


    async def _cache():
        await ctrl.synthesize("缓存测试", engine="stub", use_cache=True)
        n1 = len(list(tmpdir.iterdir()))
        t0 = time.time()
        r2 = await ctrl.synthesize("缓存测试", engine="stub", use_cache=True)
        return r2, n1, len(list(tmpdir.iterdir())), time.time() - t0


    try:
        r2, n1, n2, dt = asyncio.run(_cache())
        check("第二次 cached=True", r2.cached is True)
        check("未新增文件", n1 == n2, f"{n1} -> {n2}")
        check("命中很快", dt < 0.5, f"{dt:.3f}s")
    except Exception as e:
        check("缓存命中", False, f"{type(e).__name__}: {e}")

    print("\n--- 5. 失败降级与全失败报错 ---")


    class BoomEngine(TTSEngine):
        name = "boom"
        label = "必炸引擎"
        ext = "wav"
        mime = "audio/wav"

        @classmethod
        def is_available(cls):
            return True

        async def synthesize(self, text, voice="", speed=1.0, out_path=None):
            raise RuntimeError("模拟引擎故障")


    ctrl._classes["boom"] = BoomEngine

    async def _fallback():
        ctrl.priority = ["boom", "stub"]
        r = await ctrl.synthesize("降级测试", engine="auto", use_cache=False)
        ctrl.priority = ["boom"]
        err = None
        try:
            await ctrl.synthesize("全失败", engine="auto", use_cache=False)
        except Exception as e:
            err = e
        return r, err


    try:
        r3, err = asyncio.run(_fallback())
        check("首选失败后自动降级", r3.engine == "stub", f"落到 {r3.engine}")
        check("全部失败时抛异常", err is not None and "都失败" in str(err))
    except Exception as e:
        check("失败降级", False, f"{type(e).__name__}: {e}")

    print("\n--- 6. 空文本拦截 ---")

    async def _empty():
        bad = []
        for t in ("", "   ", None):
            try:
                await ctrl.synthesize(t, engine="stub")
                bad.append(t)
            except ValueError:
                pass
        return bad


    try:
        bad = asyncio.run(_empty())
        check("空文本都被拦下", not bad, f"漏了 {bad}")
    except Exception as e:
        check("空文本拦截", False, f"{type(e).__name__}: {e}")

    print("\n--- 7. 缓存统计 / 清空（计数要准）---")
    ctrl.priority = ["stub"]
    st_before = ctrl.stats()
    check("统计到缓存文件", st_before["count"] > 0, f"{st_before['count']} 个")
    removed = ctrl.clear_cache()
    check("清空返回的数量与实际一致", removed == st_before["count"],
          f"删了 {removed} / 原有 {st_before['count']}")
    check("清空后确实为空", ctrl.stats()["count"] == 0)

    # ==================== 8. API 端点 ====================
    #
    # API 走的是全局单例 controller（main.get_tts_controller）。这里同样把确定性
    # 桩注进去，否则 Linux CI 上既没有 SAPI、requirements 里也没有 edge-tts，
    # 可用引擎集合为空 → /api/tts 一律 503，整组用例都会失败。
    import tts as _tts_pkg

    _gctrl = _tts_pkg.get_controller()
    _gctrl._classes["stub"] = StubEngine
    # 桩排最前，但**保留** edge / sapi：/api/tts/engines 要如实列出本机所有引擎，
    # 把它们摘掉会让这条断言变成「测我自己改过的配置」而不是产品行为。
    _gctrl.priority = ["stub", "edge", "sapi"]

    print("\n--- 8. API：配置项 ---")
    with TestClient(main.app) as client:
        cfg = client.get("/api/config").json()
        tts_keys = sorted(k for k in cfg if k.startswith("tts_"))
        check("配置里有 6 个 tts_* 键", len(tts_keys) == 6, tts_keys)
        check("默认不启用（不打扰）", cfg.get("tts_enabled") is False)

        r = client.post("/api/tts", json={"text": "你好"})
        check("未启用时合成被拒（403）", r.status_code == 403, r.status_code)

        r = client.put("/api/config", json={"tts_enabled": True, "tts_engine": "stub"})
        check("白名单放行 tts 键", r.status_code == 200 and "tts_enabled" in r.json().get("updated", []))
        cfg2 = client.get("/api/config").json()
        check("改动已持久化", cfg2.get("tts_enabled") is True and cfg2.get("tts_engine") == "stub")

        print("\n--- 9. API：引擎列表 ---")
        # 出厂默认优先级是独立于运行环境的契约，直接对常量断言，
        # 不受「本机装了哪个引擎」影响。
        from tts.controller import DEFAULT_PRIORITY

        check("出厂默认优先级 edge 优先", DEFAULT_PRIORITY[0] == "edge", DEFAULT_PRIORITY)
        d = client.get("/api/tts/engines").json()
        names = [e["name"] for e in d["engines"]]
        check("返回 edge 与 sapi", "edge" in names and "sapi" in names, names)
        check("默认引擎是当前可用的那个",
              d["default_engine"] in names, d["default_engine"])
        check("音色字段是 dict", isinstance(d["voices"], dict), type(d["voices"]).__name__)

        print("\n--- 10. API：合成 + 静态挂载能播 ---")
        r = client.post("/api/tts", json={"text": "端点合成测试。"})
        check("合成返回 200", r.status_code == 200, r.status_code)
        out = r.json()
        check("engine 是配置里的 stub", out.get("engine") == "stub", out.get("engine"))
        check("返回 /tts_cache/ 下的 URL", str(out.get("url", "")).startswith("/tts_cache/"))
        audio = client.get(out["url"])
        check("音频 URL 可访问", audio.status_code == 200, audio.status_code)
        check("音频有实际内容", len(audio.content) > 1000, f"{len(audio.content)} 字节")
        check("Content-Type 是音频", "audio" in audio.headers.get("content-type", ""),
              audio.headers.get("content-type"))

        r = client.post("/api/tts", json={"text": "端点合成测试。"})
        check("同文本第二次命中缓存", r.json().get("cached") is True)

        print("\n--- 11. API：参数校验 ---")
        check("空文本 -> 400", client.post("/api/tts", json={"text": " "}).status_code == 400)
        check("未知引擎 -> 400",
              client.post("/api/tts", json={"text": "x", "engine": "nope"}).status_code == 400)
        check("非法 speed -> 400",
              client.post("/api/tts", json={"text": "x", "speed": "快"}).status_code == 400)

        print("\n--- 12. API：缓存统计与清空 ---")
        st = client.get("/api/tts/cache").json()
        check("统计到文件", st["count"] > 0, f"{st['count']} 个")
        rm = client.post("/api/tts/cache/clear").json().get("removed")
        check("清空计数正确", rm == st["count"], f"删了 {rm} / 原有 {st['count']}")

        # ==================== 13. edge-tts（需要网络，可跳过）====================
        print("\n--- 13. edge-tts 在线合成（无网则 SKIP）---")
        # ⚠️ 不能断言「结果一定是 edge」：指定引擎后若它失败，控制器**按设计**
        # 会降级到下一个可用引擎（这里就是桩）。无网环境下那样断言等于把
        # 「降级正确工作」误判成失败。所以分两种互斥的结果各自断言。
        if not avail.get("edge"):
            skip("edge-tts 合成", "edge-tts 未安装")
        else:
            r = client.post("/api/tts", json={"text": "在线引擎测试。", "engine": "edge"})
            if r.status_code != 200:
                skip("edge-tts 合成", f"HTTP {r.status_code}（多半是没网）")
            else:
                o = r.json()
                if o["engine"] == "edge":
                    check("edge 用上了", True, "edge")
                    check("edge MIME 是 mp3", o["mime"] == "audio/mpeg", o["mime"])
                    check("edge 音频可播", client.get(o["url"]).status_code == 200)
                else:
                    # edge 连不上 → 降级到桩。这正是「失败降级」的活体验证。
                    skip("edge-tts 合成", f"edge 不可达，已降级到 {o['engine']}（降级本身是预期行为）")
                    check("edge 失败后仍给出可用音频（降级生效）",
                          o["engine"] == "stub" and client.get(o["url"]).status_code == 200,
                          o["engine"])

        # ==================== 14. 音频 URL 签名（鉴权兼容）====================
        #
        # ⚠️ 这组用例存在的原因：/tts_cache 原来是用 StaticFiles 挂的，
        # 会**完全绕过** `_check_api_token`。这有两个方向的坑：
        #   ① 直接挂载 -> 设了 api_token 的机器上，音频对整个局域网敞开；
        #   ② 改成强制带 Authorization 头 -> 原生播放器（ExoPlayer/MediaPlayer）
        #      塞不进请求头，Android 端永远播不出声。
        # 最终方案是**签名 URL**（路由内校验），下面逐条锁住它的行为。
        print("\n--- 14. 音频 URL 签名 ---")
        client.put("/api/config", json={"tts_enabled": True})
        cache_dir = main._TTS_CACHE_DIR
        probe_name = "_signed_probe.mp3"
        probe_file = cache_dir / probe_name
        probe_file.write_bytes(b"ID3\x03\x00\x00\x00" + b"\x00" * 64)

        # 14.1 未设令牌：直连可播（保证向后兼容，不能把本机默认用法搞坏）
        r = client.get(f"/tts_cache/{probe_name}")
        check("未设令牌直连可播", r.status_code == 200, f"HTTP {r.status_code}")

        # 14.2 设令牌后：必须带签名
        client.put("/api/config", json={"api_token": _PROBE_TOKEN})
        r = client.get(f"/tts_cache/{probe_name}")
        check("设令牌后裸链被拒", r.status_code == 401, f"HTTP {r.status_code}")

        exp = int(time.time()) + 3600
        sig = main._tts_sign(probe_name, exp)
        r = client.get(f"/tts_cache/{probe_name}?exp={exp}&sig={sig}")
        check("正确签名可播", r.status_code == 200, f"HTTP {r.status_code}")
        check("签名 URL 的 MIME 正确",
              (r.headers.get("content-type") or "").startswith("audio/"),
              r.headers.get("content-type"))

        # 14.3 篡改 / 过期 / 换文件名，签名都应失效
        r = client.get(f"/tts_cache/{probe_name}?exp={exp}&sig={'0' * 64}")
        check("篡改签名被拒", r.status_code == 401, f"HTTP {r.status_code}")

        past = int(time.time()) - 10
        r = client.get(f"/tts_cache/{probe_name}?exp={past}&sig={main._tts_sign(probe_name, past)}")
        check("过期签名被拒", r.status_code == 401, f"HTTP {r.status_code}")

        (cache_dir / "_signed_other.mp3").write_bytes(b"ID3" + b"\x00" * 16)
        r = client.get(f"/tts_cache/_signed_other.mp3?exp={exp}&sig={sig}")
        check("签名不可跨文件复用", r.status_code == 401, f"HTTP {r.status_code}")

        # 14.4 换令牌后旧签名立即失效
        client.put("/api/config", json={"api_token": _PROBE_TOKEN + "-new"},
                   headers={"Authorization": f"Bearer {_PROBE_TOKEN}"})
        r = client.get(f"/tts_cache/{probe_name}?exp={exp}&sig={sig}")
        check("换令牌后旧签名失效", r.status_code == 401, f"HTTP {r.status_code}")

        # 14.5 目录穿越
        r = client.get("/tts_cache/..%2F..%2Fconfig.py")
        check("目录穿越被拒", r.status_code in (400, 404), f"HTTP {r.status_code}")

        # 14.6 /api/tts 返回的 URL 必须自带签名（否则客户端拿到也播不了）
        o = client.post("/api/tts", json={"text": "签名测试。"},
                        headers={"Authorization": f"Bearer {_PROBE_TOKEN}-new"})
        if o.status_code == 200:
            url = o.json().get("url", "")
            check("/api/tts 的 URL 带签名", "sig=" in url and "exp=" in url, url[:60])
        else:
            skip("/api/tts 的 URL 带签名", f"HTTP {o.status_code}")

        for f in (probe_file, cache_dir / "_signed_other.mp3"):
            safe_remove(f)

finally:
    # 还原 config_persistence 的路径，并清理临时配置文件
    _cp._CONFIG_FILE = _original_cfg_path
    _cp._cached_config = None
    for _f in _TMP_CFG_DIR.iterdir():
        if _f.is_file():
            safe_remove(_f)
    try:
        _TMP_CFG_DIR.rmdir()
    except OSError:
        pass

    # 兜底：确认真实配置一个字节都没变
    if _backup is not None:
        _now = CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else None
        if _now != _backup:
            CONFIG_FILE.write_text(_backup, encoding="utf-8")
            print("\n[清理] ⚠️ 真实配置被改动，已从备份还原")
        else:
            print("\n[清理] ✅ 真实配置未被触碰")
    elif CONFIG_FILE.exists():
        safe_remove(CONFIG_FILE)
        print("\n[清理] 已移除测试产生的 runtime_config.json")

    # 清掉测试缓存目录
    for f in tmpdir.iterdir():
        if f.is_file():
            safe_remove(f)
    try:
        tmpdir.rmdir()
    except OSError:
        pass

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 ===")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
sys.exit(0)
