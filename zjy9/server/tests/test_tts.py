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
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import main
from tts import TTSController, cache_key
from tts.base import TTSEngine

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


CONFIG_FILE = Path(main.BASE_DIR) / "data" / "runtime_config.json"
_backup = CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else None

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

    print("\n--- 2. 引擎可用性 ---")
    avail = {a["name"]: a["available"] for a in ctrl.available()}
    print(f"    {avail}")
    check("注册了 edge 与 sapi", set(avail) == {"edge", "sapi"})
    check("SAPI 在 Windows 上可用", avail.get("sapi") is True)

    print("\n--- 3. SAPI 真实合成（离线，必须能过）---")


    async def _sapi():
        return await ctrl.synthesize("你好，我是测试。", engine="sapi", use_cache=False)


    try:
        r = asyncio.run(_sapi())
        check("SAPI 产出文件", r.path.exists() and r.path.stat().st_size > 1000,
              f"{r.path.stat().st_size} 字节")
        check("SAPI MIME 是 wav", r.mime == "audio/wav")
        check("SAPI 读出了时长", r.duration_ms > 0, f"{r.duration_ms}ms")
        check("engine 字段是 sapi", r.engine == "sapi")
    except Exception as e:
        check("SAPI 真实合成", False, f"{type(e).__name__}: {e}")

    print("\n--- 4. 缓存命中 ---")


    async def _cache():
        await ctrl.synthesize("缓存测试", engine="sapi", use_cache=True)
        n1 = len(list(tmpdir.iterdir()))
        t0 = time.time()
        r2 = await ctrl.synthesize("缓存测试", engine="sapi", use_cache=True)
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
        ctrl.priority = ["boom", "sapi"]
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
        check("首选失败后自动降级", r3.engine == "sapi", f"落到 {r3.engine}")
        check("全部失败时抛异常", err is not None and "都失败" in str(err))
    except Exception as e:
        check("失败降级", False, f"{type(e).__name__}: {e}")

    print("\n--- 6. 空文本拦截 ---")

    async def _empty():
        bad = []
        for t in ("", "   ", None):
            try:
                await ctrl.synthesize(t, engine="sapi")
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
    ctrl.priority = ["sapi"]
    st_before = ctrl.stats()
    check("统计到缓存文件", st_before["count"] > 0, f"{st_before['count']} 个")
    removed = ctrl.clear_cache()
    check("清空返回的数量与实际一致", removed == st_before["count"],
          f"删了 {removed} / 原有 {st_before['count']}")
    check("清空后确实为空", ctrl.stats()["count"] == 0)

    # ==================== 8. API 端点 ====================
    print("\n--- 8. API：配置项 ---")
    with TestClient(main.app) as client:
        cfg = client.get("/api/config").json()
        tts_keys = sorted(k for k in cfg if k.startswith("tts_"))
        check("配置里有 6 个 tts_* 键", len(tts_keys) == 6, tts_keys)
        check("默认不启用（不打扰）", cfg.get("tts_enabled") is False)

        r = client.post("/api/tts", json={"text": "你好"})
        check("未启用时合成被拒（403）", r.status_code == 403, r.status_code)

        r = client.put("/api/config", json={"tts_enabled": True, "tts_engine": "sapi"})
        check("白名单放行 tts 键", r.status_code == 200 and "tts_enabled" in r.json().get("updated", []))
        cfg2 = client.get("/api/config").json()
        check("改动已持久化", cfg2.get("tts_enabled") is True and cfg2.get("tts_engine") == "sapi")

        print("\n--- 9. API：引擎列表 ---")
        d = client.get("/api/tts/engines").json()
        names = [e["name"] for e in d["engines"]]
        check("返回 edge 与 sapi", "edge" in names and "sapi" in names, names)
        check("默认引擎是 edge", d["default_engine"] == "edge", d["default_engine"])
        sapi_voices = d["voices"].get("sapi", [])
        check("SAPI 音色列表可用", isinstance(sapi_voices, list))

        print("\n--- 10. API：合成 + 静态挂载能播 ---")
        r = client.post("/api/tts", json={"text": "端点合成测试。"})
        check("合成返回 200", r.status_code == 200, r.status_code)
        out = r.json()
        check("engine 是配置里的 sapi", out.get("engine") == "sapi", out.get("engine"))
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
        if not avail.get("edge"):
            skip("edge-tts 合成", "edge-tts 未安装")
        else:
            r = client.post("/api/tts", json={"text": "在线引擎测试。", "engine": "edge"})
            if r.status_code == 200:
                o = r.json()
                check("edge 用上了", o["engine"] == "edge", o["engine"])
                check("edge MIME 是 mp3", o["mime"] == "audio/mpeg", o["mime"])
                check("edge 音频可播", client.get(o["url"]).status_code == 200)
            else:
                skip("edge-tts 合成", f"HTTP {r.status_code}（多半是没网）")

finally:
    # 恢复运行时配置，别污染用户环境
    if _backup is not None:
        CONFIG_FILE.write_text(_backup, encoding="utf-8")
        print("\n[清理] runtime_config.json 已还原")
    else:
        if CONFIG_FILE.exists():
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
