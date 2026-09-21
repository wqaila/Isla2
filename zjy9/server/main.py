"""
爱莉希雅 AI 聊天服务 - 主入口
基于 FastAPI，提供 REST API + WebSocket + 监控面板
"""
import sys
import json
import uuid
import hmac
import asyncio
import socket
import time
import re
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from collections import defaultdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

import database as db
from config import (
    SERVER_HOST, SERVER_PORT, STATIC_DIR, MAX_CONNECTIONS,
    MODEL_SOURCE, CLOUD_USE_FEWSHOT,
    API_TOKEN, RATE_LIMIT_PER_MINUTE, CHAT_RATE_LIMIT_PER_MINUTE,
    REQUEST_TIMEOUT, CORS_ORIGINS, ensure_directories, runtime,
)
from ollama_client import ollama_client, GENERATION_OPTIONS, MAX_OUTPUT_CHARS
from cloud_client import cloud_client, CLOUD_PROVIDERS
from memory import memory_manager
from connection_manager import connection_manager
from logger_service import logger
from config_persistence import load_config, save_config
from user_learning import learning_engine
from emotion_engine import get_emotion_engine, get_mood_tracker, analyze_generated_response, check_response_consistency
from time_context import build_time_enhanced_context

# 服务启动时间（用于健康检查里的 uptime，而不是拿系统开机时间冒充）
SERVICE_START_TS = time.time()


# ===== API Token 认证（仅在 HTTP 中间件中处理，避免影响 WebSocket） =====
def _current_api_token() -> str:
    """读取当前生效的 API Token。

    之前这里直接用 config.API_TOKEN 这个模块常量，导致通过
    PUT /api/config 设置 api_token 后完全不生效（常量在启动时就固化了），
    用户以为设了密码、实际门还开着。改为每次从运行时配置读取。
    """
    try:
        return str(runtime("api_token", API_TOKEN) or "")
    except Exception:
        return API_TOKEN


def _check_api_token(path: str, auth_header: str | None) -> bool:
    """
    检查 API Token，返回 True 表示允许访问
    """
    token_expected = _current_api_token()
    if not token_expected:
        return True

    # 免认证路径：只保留「静态资源 / WebSocket / 健康探测 / 面板页面」。
    # ⚠️ 之前把 /api/config、/api/memory、/api/learning、/api/emotion、
    #    /api/logs、/api/model 都列进了白名单，等于这些管理接口
    #    （改配置、清空记忆、重置画像）对整个局域网敞开，认证形同虚设。
    #    另外 "/api/session" 会前缀匹配到 "/api/sessions"，也一并去掉。
    public_prefixes = (
        "/static",
        "/ws",
    )
    public_paths = {"/", "/health", "/dashboard", "/docs", "/openapi.json", "/redoc"}
    if path in public_paths or path.startswith(public_prefixes):
        return True

    # 检查 Authorization header
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # 用常量时间比较，避免时序侧信道
        if hmac.compare_digest(token, token_expected):
            return True
    return False


# ===== 速率限制 =====
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str, limit: int, window: int = 60) -> bool:
    """检查速率限制，返回 True 表示允许"""
    if limit <= 0:
        return True
    now = time.time()
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < window
    ]
    if len(_rate_limit_store[client_ip]) >= limit:
        return False
    _rate_limit_store[client_ip].append(now)
    return True


async def _cleanup_rate_limits():
    """定期清理过期的速率限制记录"""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired_ips = [
            ip for ip, timestamps in _rate_limit_store.items()
            if not timestamps or all(now - t > 60 for t in timestamps)
        ]
        for ip in expired_ips:
            del _rate_limit_store[ip]


# ===== 生命周期（FIX #13+#14: 合并 on_event，添加优雅关闭） =====

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    ensure_directories()
    db.init_db()
    # 把事件循环交给日志服务，供后台线程安全地广播日志
    logger.set_loop(asyncio.get_running_loop())
    local_ip = get_local_ip()
    logger.info("system", f"服务启动于 {SERVER_HOST}:{SERVER_PORT}")
    logger.info("system", f"本机 IP: {local_ip}")
    logger.info("system", f"监控面板: http://localhost:{SERVER_PORT}/dashboard")

    # 检查 Ollama 状态
    status = await ollama_client.check_status()
    if status["status"] == "running":
        logger.info("model", f"Ollama 运行中，模型: {status.get('models', [])}")
    else:
        logger.warning("model", f"Ollama 未运行: {status.get('error', 'unknown')}")

    # 启动定期清理任务（FIX #13: 替代 deprecated on_event）
    cleanup_task = asyncio.create_task(_cleanup_rate_limits())

    yield

    # 优雅关闭（FIX #14）
    logger.info("system", "服务正在关闭...")
    cleanup_task.cancel()
    await ollama_client.close()
    await cloud_client.close()
    # 断开所有 WebSocket
    for conn_id in list(connection_manager.active_connections.keys()):
        await connection_manager.disconnect(conn_id)
    # 把内存里还没落盘的记忆刷到磁盘
    try:
        memory_manager.flush()
    except Exception:
        pass
    # 等日志队列写完，再关闭所有数据库连接
    logger.shutdown()
    db.close_all_connections()


app = FastAPI(
    title="爱莉希雅 AI 聊天服务",
    description="本地 LLM 推理服务，支持手机远程调用",
    version="1.0.0",
    lifespan=lifespan,
)

# ===== CORS 中间件 =====
# 原实现是 allow_origins=["*"]（对所有网站开放）。
# 改为从 config.CORS_ORIGINS 读取，默认只允许本机来源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 全局异常处理器 =====
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理，防止未捕获异常导致服务崩溃

    注意：详细堆栈只写日志，返回给客户端的只有通用提示，
    避免把内部路径/实现细节泄露出去。
    """
    logger.error("system", f"未捕获异常: {request.method} {request.url.path} - {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请查看服务端日志"},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP 异常处理"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# ===== 速率限制中间件 =====
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """速率限制 + Token 认证中间件"""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path
    
    # WebSocket 和静态文件跳过
    if path.startswith("/static") or path.startswith("/ws"):
        return await call_next(request)
    
    # API Token 认证
    auth_header = request.headers.get("authorization")
    if not _check_api_token(path, auth_header):
        return JSONResponse(
            status_code=401,
            content={"detail": "未授权：请提供有效的 API Token"},
        )
    
    # 速率限制（从运行时配置读取，保证 PUT /api/config 改完立刻生效）
    if path == "/api/chat":
        limit = int(runtime("chat_rate_limit_per_minute", CHAT_RATE_LIMIT_PER_MINUTE))
    else:
        limit = int(runtime("rate_limit_per_minute", RATE_LIMIT_PER_MINUTE))
    if not _check_rate_limit(client_ip, limit):
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试", "retry_after": 60},
        )
    return await call_next(request)


# ===== 数据模型 =====

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str  # FIX #10: 移除 max_length 限制（通过中间件控制）
    session_id: str | None = None
    device_id: str = "anonymous"
    device_name: str = ""
    stream: bool = True


class SessionCreate(BaseModel):
    """创建会话请求"""
    device_id: str = "anonymous"
    device_name: str = ""
    connection_type: str = "wifi"


# ===== 工具函数 =====

def get_local_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ===== REST API =====

@app.get("/")
async def root():
    """服务首页"""
    return {
        "name": "爱莉希雅 AI 聊天服务",
        "version": "1.0.0",
        "status": "running",
        "dashboard": "/dashboard",
        "api_docs": "/docs",
        "endpoints": {
            "chat": "/api/chat",
            "sessions": "/api/sessions",
            "messages": "/api/messages/{session_id}",
            "status": "/api/status",
            "logs": "/api/logs",
        }
    }


@app.get("/health")
async def health_check():
    """健康检查端点（供客户端轻量级探测）"""
    ollama_ok = False
    try:
        status = await ollama_client.check_status()
        ollama_ok = status.get("status") == "running"
    except Exception:
        pass
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "ollama": ollama_ok,
        "cloud_configured": cloud_client.is_configured(),
        "active_connections": connection_manager.get_active_count(),
    }


@app.get("/api/status")
async def get_status():
    """获取服务状态"""
    ollama_status = await ollama_client.check_status()
    stats = db.get_stats()
    connections = connection_manager.get_active_list()

    return {
        "server": {
            "status": "running",
            "host": SERVER_HOST,
            "port": SERVER_PORT,
            "local_ip": get_local_ip(),
            "uptime_seconds": int(time.time() - SERVICE_START_TS),
            "started_at": datetime.fromtimestamp(SERVICE_START_TS).isoformat(),
            "now": datetime.now().isoformat(),
        },
        "ollama": ollama_status,
        "stats": stats,
        "connections": {
            "active_count": len(connections),
            "devices": connections,
        },
    }


@app.post("/api/sessions")
async def create_session(req: SessionCreate):
    """创建新的聊天会话"""
    session_id = db.create_session(req.device_id, req.device_name, req.connection_type)
    logger.info("chat", f"新会话创建: {session_id[:8]}... (设备: {req.device_name})")
    return {"session_id": session_id}


@app.get("/api/sessions")
async def list_sessions(limit: int = 20):
    """获取最近的会话列表"""
    sessions = db.get_recent_sessions(limit)
    return {"sessions": sessions}


@app.get("/api/messages/search")
async def search_messages(q: str, session_id: str = None, limit: int = 50):
    """搜索消息内容

    ⚠️ 路由顺序很重要：本路由必须注册在 /api/messages/{session_id} **之前**，
    否则 FastAPI 会先用路径参数路由匹配掉 /api/messages/search，
    把 "search" 当成 session_id，导致搜索接口永远不可用。
    """
    results = db.search_messages(q, session_id=session_id, limit=limit)
    return {"results": results, "count": len(results)}


@app.get("/api/messages/{session_id}")
async def get_messages(session_id: str, limit: int = 100):
    """获取会话最近的消息历史（按时间正序）"""
    messages = db.get_recent_messages(session_id, limit)
    return {"messages": messages}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其所有消息"""
    success = db.delete_session(session_id)
    if success:
        logger.info("chat", f"会话已删除: {session_id[:8]}...")
        return {"status": "ok", "message": "会话已删除"}
    raise HTTPException(status_code=404, detail="会话不存在")


@app.put("/api/sessions/{session_id}/close")
async def close_session(session_id: str):
    """关闭会话"""
    db.close_session(session_id)
    return {"status": "ok", "message": "会话已关闭"}


@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: str):
    """删除单条消息"""
    success = db.delete_message(message_id)
    if success:
        return {"status": "ok", "message": "消息已删除"}
    raise HTTPException(status_code=404, detail="消息不存在")


# ===== 强制终止聊天推理 =====

# 按会话记录终止请求。
# 之前是一个全局 "rest" 标志，任何一个用户点"终止"都会打断所有正在流式输出的回复；
# 而且 WebSocket 路径根本不检查这个标志，手机上无法终止。
_chat_stop_flags: dict[str, bool] = {}


def _request_stop(session_id: str):
    _chat_stop_flags[session_id] = True


def _consume_stop(session_id: str) -> bool:
    """读取并清除指定会话的终止标志"""
    return bool(_chat_stop_flags.pop(session_id, False))


def _clear_stop(session_id: str):
    _chat_stop_flags.pop(session_id, None)


@app.post("/api/chat/stop")
async def stop_chat(session_id: str = ""):
    """强制终止指定会话正在进行的推理（不传 session_id 则终止全部）"""
    if session_id:
        _request_stop(session_id)
        logger.info("chat", f"收到强制终止请求: {session_id[:8]}...")
    else:
        for sid in list(_chat_stop_flags.keys()):
            _chat_stop_flags[sid] = True
        # 用一个通配键，让尚未注册的会话也能收到终止信号
        _chat_stop_flags["*"] = True
        logger.info("chat", "收到强制终止请求（全部会话）")
    return {"status": "ok", "message": "已发送终止信号"}


def _is_stopped(session_id: str) -> bool:
    """判断某会话是否被要求终止"""
    if _chat_stop_flags.get("*"):
        return True
    return _consume_stop(session_id)


# ===== 运行时配置管理 =====

@app.get("/api/config")
async def get_runtime_config():
    """获取运行时配置"""
    config = load_config()
    # 隐藏敏感字段
    if config.get("cloud_api_key"):
        config["cloud_api_key"] = "***已设置***"
    return config


@app.put("/api/config")
async def update_runtime_config(updates: dict):
    """更新运行时配置（持久化到文件）"""
    # 过滤不允许修改的字段
    allowed_keys = {
        "cloud_provider", "cloud_api_key", "cloud_model", "cloud_temperature",
        "cloud_max_tokens", "cloud_use_fewshot", "model_source", "api_token",
        "rate_limit_per_minute", "chat_rate_limit_per_minute", "memory_backend",
        "memory_max_conversations", "memory_max_facts", "memory_max_summaries",
        "request_timeout", "max_connections", "heartbeat_interval",
        # 上下文相关（直接影响人设是否被截断）
        "num_ctx", "use_fewshot_local", "memory_context_max_chars",
        "learning_context_max_chars", "max_history_messages",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed_keys}
    if not filtered:
        raise HTTPException(status_code=400, detail="没有有效的配置项")
    
    config = save_config(filtered)
    logger.info("config", f"运行时配置已更新: {list(filtered.keys())}")
    return {"status": "ok", "updated": list(filtered.keys())}


# ===== 记忆自动清理 =====

@app.post("/api/memory/cleanup")
async def trigger_memory_cleanup():
    """手动触发记忆清理"""
    stats_before = memory_manager.get_stats()
    memory_manager.auto_cleanup()
    stats_after = memory_manager.get_stats()
    return {
        "status": "ok",
        "before": stats_before,
        "after": stats_after,
    }


def _history_limit() -> int:
    """单次请求携带的历史消息条数（可运行时配置）"""
    try:
        return max(2, int(runtime("max_history_messages", 20)))
    except Exception:
        return 20


async def _build_chat_context(session_id: str, device_id: str, user_message: str) -> dict:
    """构建一次对话所需的全部上下文。

    REST 与 WebSocket 两条路径原本各写了一遍这套逻辑（历史、记忆、时间、
    情感），任何改动都要改两处，并且已经出现过"两边一起把历史取反"的情况。
    这里抽成唯一实现。
    """
    # ⚠️ 必须取「最近」若干条，而不是「最早」若干条。
    #    原实现用 ORDER BY created_at ASC LIMIT 7，导致聊到第 N 轮时
    #    喂给模型的还是第 1-7 条，多轮记忆永远卡在开头。
    recent = db.get_recent_messages(session_id, limit=_history_limit())
    history = [{"role": m["role"], "content": m["content"]} for m in recent]
    # 去掉刚保存的这条用户消息（get_ai_stream 会把当前消息单独追加）
    if history and history[-1]["role"] == "user" and history[-1]["content"] == user_message:
        history = history[:-1]
    # 过滤掉过长的助手回复（可能是旧的垃圾输出）
    history = [m for m in history if m["role"] == "user" or len(m["content"]) <= 300]

    memory_context = memory_manager.get_memory_context(
        user_message,
        max_chars=int(runtime("memory_context_max_chars", 800)),
    )

    time_context = build_time_enhanced_context(
        user_message, session_id, db.get_message_count(session_id)
    )

    emotion_engine = get_emotion_engine()
    emotion_result = emotion_engine.analyze(user_message)
    mood_tracker = get_mood_tracker(session_id)
    mood_tracker.record(emotion_result)
    emotion_context = emotion_engine.get_adaptive_instruction(emotion_result)
    trend_instruction = mood_tracker.get_trend_instruction(window=10)
    if trend_instruction:
        emotion_context += "\n\n" + trend_instruction

    return {
        "history": history,
        "memory_context": memory_context,
        "time_context": time_context,
        "emotion_context": emotion_context,
        "emotion_result": emotion_result,
    }


def _finish_turn(session_id: str, device_id: str, user_message: str,
                 full_content: str, tokens: int, response_ms: int):
    """一次对话的收尾：落库 + 写记忆 + 触发学习引擎（两条路径共用）"""
    db.save_message(session_id, "assistant", full_content, tokens, response_ms)
    logger.chat_log(session_id, "assistant", full_content, tokens, response_ms)
    memory_manager.add_conversation(session_id, user_message, full_content)
    learn_result = learning_engine.learn_from_message(
        user_message, full_content, session_id, device_id=device_id
    )
    if learn_result.get("needs_llm_analysis"):
        asyncio.create_task(
            _run_llm_learning_analysis(learn_result["llm_prompt"], device_id)
        )
    return learn_result


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    聊天 API（REST 方式）
    支持流式和非流式返回
    """
    # 获取或创建会话
    session_id = req.session_id
    if not session_id:
        session_id = db.create_session(req.device_id, req.device_name, "wifi")

    # 把学习引擎切到该设备的画像（多用户/多设备隔离）
    learning_engine.use_device(req.device_id)

    # 保存用户消息
    db.save_message(session_id, "user", req.message)
    logger.chat_log(session_id, "user", req.message)

    # 构建上下文（历史/记忆/时间/情感）——REST 与 WebSocket 共用同一实现
    ctx = await _build_chat_context(session_id, req.device_id, req.message)
    history = ctx["history"]
    memory_context = ctx["memory_context"]
    time_context = ctx["time_context"]
    emotion_context = ctx["emotion_context"]
    emotion_result = ctx["emotion_result"]

    if req.stream:
        # 流式返回 - 使用 Server-Sent Events
        from fastapi.responses import StreamingResponse

        # 清除该会话可能残留的终止标志
        _clear_stop(session_id)

        async def generate():
            full_content = ""
            tokens = 0
            response_ms = 0
            finished = False
            try:
                # FIX: 为流式生成添加整体超时保护
                stream_gen = get_ai_stream(req.message, history, memory_context=memory_context, session_id=session_id, emotion_context=emotion_context, time_context=time_context)
                async for chunk in stream_gen:
                    # 检查终止标志（按会话隔离，不会误伤其他用户）
                    if _is_stopped(session_id):
                        logger.info("chat", "推理已被用户强制终止")
                        stop_suffix = "\n\n⏹ [已手动终止]"
                        full_content += stop_suffix
                        _finish_turn(session_id, req.device_id, req.message,
                                     full_content, tokens, response_ms)
                        finished = True
                        yield "data: " + json.dumps({"content": stop_suffix, "done": True, "tokens": tokens, "response_ms": response_ms}) + "\n\n"
                        return

                    # FIX: done chunk 也可能包含内容（如错误消息），不能丢弃
                    if chunk.get("content"):
                        full_content += chunk["content"]
                        yield f"data: {json.dumps({'content': chunk['content'], 'done': False})}\n\n"
                    if chunk.get("done"):
                        tokens = chunk.get("total_tokens", 0)
                        response_ms = chunk.get("response_time_ms", 0)
                        _finish_turn(session_id, req.device_id, req.message,
                                     full_content, tokens, response_ms)
                        finished = True
                        # ==== AI 回复情绪分析 + 一致性检查 ====
                        resp_emotion = analyze_generated_response(full_content, emotion_result) if len(full_content) > 5 else None
                        consistency = check_response_consistency(req.message, full_content) if len(full_content) > 5 else None
                        done_metadata = {
                            "content": "", "done": True,
                            "tokens": tokens, "response_ms": response_ms,
                        }
                        if resp_emotion:
                            done_metadata["response_emotion"] = resp_emotion
                        if consistency:
                            done_metadata["consistency"] = consistency
                        yield f"data: {json.dumps(done_metadata)}\n\n"
            except asyncio.TimeoutError:
                timeout_s = int(runtime("request_timeout", REQUEST_TIMEOUT))
                logger.error("chat", f"REST 流式聊天超时 ({timeout_s}s)")
                error_msg = f"\n\n[错误] 回复超时（{timeout_s}秒），模型可能正在加载中，请重试"
                yield f"data: {json.dumps({'content': error_msg, 'done': True, 'tokens': 0, 'response_ms': 0})}\n\n"
            except Exception as e:
                # FIX #4: 流式异常时通知客户端
                logger.error("chat", f"流式聊天异常: {str(e)}")
                error_msg = f"\n\n[错误] {str(e)[:100]}"
                yield f"data: {json.dumps({'content': error_msg, 'done': True, 'tokens': 0, 'response_ms': 0})}\n\n"
            finally:
                # 客户端中途断开（GeneratorExit）等情况：只要已经产出内容就落库，
                # 否则这段回复会凭空消失
                if not finished and full_content:
                    try:
                        _finish_turn(session_id, req.device_id, req.message,
                                     full_content, tokens, response_ms)
                    except Exception as e:
                        logger.warning("chat", f"中断回复落库失败: {str(e)[:100]}")

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        # 非流式返回
        full_content = ""
        tokens = 0
        response_ms = 0
        async for chunk in get_ai_stream(req.message, history, memory_context=memory_context, session_id=session_id, emotion_context=emotion_context, time_context=time_context):
            # 用 .get 兜底：个别 chunk（例如只带 done 标记的收尾块）可能没有 content 键
            full_content += chunk.get("content", "") or ""
            tokens = chunk.get("total_tokens", tokens) or tokens
            response_ms = chunk.get("response_time_ms", response_ms) or response_ms

        # 落库 + 记忆 + 学习引擎（与流式路径共用同一实现）
        _finish_turn(session_id, req.device_id, req.message,
                     full_content, tokens, response_ms)

        # ==== AI 回复情绪分析 + 一致性检查 ====
        resp_emotion = analyze_generated_response(full_content, emotion_result) if len(full_content) > 5 else None
        consistency = check_response_consistency(req.message, full_content) if len(full_content) > 5 else None
        response_data = {
            "session_id": session_id,
            "content": full_content,
            "tokens": tokens,
            "response_ms": response_ms,
        }
        if resp_emotion:
            response_data["response_emotion"] = resp_emotion
        if consistency:
            response_data["consistency"] = consistency
        return response_data


async def _run_llm_learning_analysis(llm_prompt: str, device_id: str = ""):
    """异步执行 LLM 深度用户分析（按设备写回画像）"""
    try:
        analysis_messages = [
            {"role": "system", "content": "你是一个用户行为分析专家，只返回 JSON 格式的分析结果。"},
            {"role": "user", "content": llm_prompt},
        ]
        full_response = ""
        
        # 选择模型：优先使用 Ollama（如果可用），否则用云端 API
        use_cloud = False
        if MODEL_SOURCE == "cloud":
            use_cloud = True
        elif MODEL_SOURCE == "auto":
            ollama_status = await ollama_client.check_status()
            if ollama_status["status"] != "running" or not ollama_status.get("model_available", False):
                use_cloud = True
        
        if use_cloud:
            if cloud_client.is_configured():
                # FIX #3: 直接调用底层 HTTP API，避免 cloud_client.chat() 注入角色人设
                import httpx as _httpx
                _headers = {
                    "Authorization": f"Bearer {cloud_client.api_key}",
                    "Content-Type": "application/json",
                }
                _payload = {
                    "model": cloud_client.model,
                    "messages": analysis_messages,
                    "temperature": 0.3,
                    "max_tokens": 512,
                    "stream": False,
                }
                try:
                    _resp = await cloud_client._client.post(
                        f"{cloud_client.base_url}/chat/completions",
                        headers=_headers,
                        json=_payload,
                    )
                    if _resp.status_code == 200:
                        _data = _resp.json()
                        full_response = _data.get("choices", [{}])[0].get("message", {}).get("content", "")
                except Exception as _e:
                    logger.warning("learning", f"云端 LLM 分析请求失败: {str(_e)[:100]}")
                    return
            else:
                logger.warning("learning", "LLM 分析跳过：无可用模型")
                return
        else:
            async for chunk in ollama_client.chat(analysis_messages, stream=False):
                full_response += chunk.get("content", "")
            
        # FIX #3: 尝试解析 JSON（Ollama 和云端共用，移到 if/else 外部）
        if full_response.strip():
            json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
                learning_engine.apply_llm_analysis(analysis, device_id=device_id)
                logger.info("learning", "LLM 深度分析完成")
    except Exception as e:
        logger.warning("learning", f"LLM 分析失败（非致命）: {str(e)[:100]}")
    finally:
        # 无论成功失败，都重置 pending 标记
        learning_engine.on_llm_analysis_complete()


@app.get("/api/logs")
async def get_logs(limit: int = 100, level: str = None, category: str = None):
    """获取系统日志"""
    logs = db.get_system_logs(limit, level)
    if category:
        logs = [l for l in logs if l.get("category") == category]
    return {"logs": logs}


@app.get("/api/logs/connections")
async def get_connection_logs(limit: int = 100):
    """获取连接日志"""
    logs = db.get_connection_logs(limit)
    return {"logs": logs}


@app.get("/api/model/check")
@app.post("/api/model/check")
async def check_model():
    """检查 Ollama 服务状态（支持 GET 和 POST）"""
    status = await ollama_client.check_status()
    return status


# ===== 云端 API 管理 =====

class CloudConfigRequest(BaseModel):
    """云端 API 配置请求"""
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    use_fewshot: bool | None = None
    model_source: str | None = None  # "ollama", "cloud", "auto"


@app.get("/api/cloud/providers")
async def get_cloud_providers():
    """获取支持的云端 API 提供商列表"""
    providers = []
    for key, info in CLOUD_PROVIDERS.items():
        providers.append({
            "id": key,
            "name": info["name"],
            "description": info["description"],
            "base_url": info["base_url"],
            "default_model": info["model"],
        })
    return {"providers": providers, "current": cloud_client.provider}


@app.get("/api/cloud/config")
async def get_cloud_config():
    """获取当前云端 API 配置"""
    runtime = load_config()
    config = cloud_client.get_config()
    config["model_source"] = runtime.get("model_source", MODEL_SOURCE)
    return config


@app.post("/api/cloud/config")
async def update_cloud_config(req: CloudConfigRequest):
    """更新云端 API 配置"""
    cloud_client.configure(
        provider=req.provider,
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        use_fewshot=req.use_fewshot,
    )
    # 保存 model_source 到运行时配置
    if req.model_source is not None:
        save_config({"model_source": req.model_source})
        logger.info("config", f"模型路由已更新: {req.model_source}")
    logger.info("cloud", f"云端 API 配置已更新: {cloud_client.provider} / {cloud_client.model}")
    return {"status": "ok", "config": cloud_client.get_config()}


@app.post("/api/cloud/test")
async def test_cloud_connection():
    """测试云端 API 连接"""
    result = await cloud_client.test_connection()
    if result["status"] == "success":
        logger.info("cloud", f"云端 API 测试成功: {result['message']}")
    else:
        logger.warning("cloud", f"云端 API 测试失败: {result['message']}")
    return result


# ===== 统一聊天路由 =====

async def get_ai_stream(user_message: str, history: list, source: str = None,
                        memory_context: str = "", session_id: str = "",
                        emotion_context: str = "", time_context: str = ""):
    """
    统一的 AI 流式回复路由
    
    Args:
        user_message: 用户最新消息
        history: 历史消息 [{"role": "user/assistant", "content": "..."}]
        source: 模型来源（"ollama"/"cloud"/None=使用配置）
        memory_context: RAG 记忆上下文
        session_id: 会话 ID（用于记忆提取）
        emotion_context: 情感识别引擎的情绪自适应指导
        time_context: 时间上下文感知信息
    
    Yields:
        {"content": "...", "done": bool, ...}
    """
    runtime = load_config()
    use_source = source or runtime.get("model_source", MODEL_SOURCE)
    
    # 提取用户关键信息到记忆
    memory_manager.extract_and_save_facts(user_message, "", session_id)
    
    # 获取用户学习引擎的个性化上下文
    learning_context = learning_engine.get_personalized_context(
        current_message=user_message,
        max_chars=int(runtime("learning_context_max_chars", 600)),
    )
    
    # auto 模式：优先 Ollama，不可用则用云端
    if use_source == "auto":
        ollama_status = await ollama_client.check_status()
        if ollama_status["status"] == "running" and ollama_status.get("model_available", False):
            use_source = "ollama"
        elif cloud_client.is_configured():
            use_source = "cloud"
            logger.info("model", "Ollama 不可用，自动切换到云端 API")
        else:
            yield {
                "content": "Ollama 和云端 API 均不可用，请检查配置~",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": 0,
            }
            return
    
    if use_source == "cloud":
        if not cloud_client.is_configured():
            yield {
                "content": "云端 API 未配置，请在设置中填写 API Key~",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": 0,
            }
            return
        # 云端路径：build_messages 自动注入人设 + 时间上下文 + Few-Shot + 记忆 + 学习上下文 + 情感指导
        async for chunk in cloud_client.chat(user_message, history, memory_context=memory_context, learning_context=learning_context, emotion_context=emotion_context, time_context=time_context):
            yield chunk
    else:
        # Ollama 路径：手动构建含人设 + Few-Shot + 记忆 + 学习上下文 + 情感指导的完整消息列表
        from elysia_prompt import ELYSIA_SYSTEM_PROMPT, ELYSIA_FEWSHOT_EXAMPLES
        full_messages = []
        # System Prompt + 时间上下文 + 记忆上下文 + 学习上下文 + 情感指导
        system_content = ELYSIA_SYSTEM_PROMPT
        if time_context:
            system_content += f"\n\n{time_context}"
        if memory_context:
            system_content += f"\n\n以下是你从之前的对话中记住的关于用户的信息，在回答时请自然地融入这些记忆：\n{memory_context}"
        if learning_context:
            system_content += f"\n\n以下是你通过长期学习了解到的用户画像，请根据这些信息调整你的回复风格和内容：\n{learning_context}"
        if emotion_context:
            system_content += f"\n\n{emotion_context}"
        full_messages.append({"role": "system", "content": system_content})
        # Few-Shot 示例：占约 500 token 上下文，可通过运行时配置 use_fewshot_local 关闭。
        # 注意：这里原本读的是 CLOUD_USE_FEWSHOT（一个"云端"配置项），
        # 却用来控制本地 Ollama 路径，语义是错的。
        if runtime("use_fewshot_local", CLOUD_USE_FEWSHOT):
            full_messages.extend(ELYSIA_FEWSHOT_EXAMPLES)
        # 历史消息（条数可运行时配置）
        history_limit = _history_limit()
        recent = history[-history_limit:] if len(history) > history_limit else history
        full_messages.extend(recent)
        # 用户最新消息
        full_messages.append({"role": "user", "content": user_message})
        
        async for chunk in ollama_client.chat(full_messages, stream=True):
            yield chunk


# ===== WebSocket 端点 =====

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket 聊天端点
    客户端连接后通过 JSON 消息进行实时聊天
    """
    connection_id = str(uuid.uuid4())
    device_id = websocket.query_params.get("device_id", "anonymous")
    device_name = websocket.query_params.get("device_name", "")
    session_id = websocket.query_params.get("session_id", "")

    # 检查连接数限制（从运行时配置读取）
    max_conn = int(runtime("max_connections", MAX_CONNECTIONS))
    if connection_manager.get_active_count() >= max_conn:
        await websocket.close(code=1013, reason="服务器连接数已满")
        return

    await connection_manager.connect(websocket, connection_id, device_id, device_name)
    logger.connection_log("connected", device_id, device_name, "wifi",
                          websocket.client.host if websocket.client else "")

    # 自动创建会话
    if not session_id:
        session_id = db.create_session(device_id, device_name, "wifi")

    # 发送欢迎消息
    await connection_manager.send_to(connection_id, {
        "type": "connected",
        "connection_id": connection_id,
        "session_id": session_id,
        "message": "已连接到爱莉希雅 AI 服务",
    })

    try:
        while True:
            # 接收消息
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"type": "chat", "message": raw}

            msg_type = data.get("type", "chat")

            if msg_type == "heartbeat":
                # 心跳
                await connection_manager.send_to(connection_id, {
                    "type": "heartbeat_ack",
                    "time": datetime.now().isoformat(),
                })

            elif msg_type == "chat":
                user_message = data.get("message", "").strip()
                if not user_message:
                    continue

                # FIX #5: 限制消息长度
                if len(user_message) > 10000:
                    await connection_manager.send_to(connection_id, {
                        "type": "error",
                        "message": "消息过长，最大支持 10000 字符",
                    })
                    continue

                db.save_message(session_id, "user", user_message)
                logger.chat_log(session_id, "user", user_message)

                # 把学习引擎切到该设备的画像（多用户/多设备隔离）
                learning_engine.use_device(device_id)

                # 构建上下文（历史/记忆/时间/情感）——与 REST 路径共用同一实现
                ctx = await _build_chat_context(session_id, device_id, user_message)
                history = ctx["history"]
                memory_context = ctx["memory_context"]
                time_context = ctx["time_context"]
                emotion_context = ctx["emotion_context"]
                emotion_result = ctx["emotion_result"]

                # 通过 WebSocket 推送情感分析结果给客户端（用于 UI 显示）
                await connection_manager.send_to(connection_id, {
                    "type": "emotion",
                    "dominant": emotion_result.dominant,
                    "emoji": emotion_result.emoji,
                    "intensity": emotion_result.intensity,
                    "valence": emotion_result.valence,
                    "summary": emotion_result.summary,
                })

                full_content = ""
                tokens = 0
                response_ms = 0
                stopped = False

                # 清除该会话可能残留的终止标志
                _clear_stop(session_id)
                timeout_s = int(runtime("request_timeout", REQUEST_TIMEOUT))

                try:
                    # FIX #5: 添加超时机制
                    async def _stream_reply():
                        nonlocal full_content, tokens, response_ms, stopped
                        async for chunk in get_ai_stream(user_message, history, memory_context=memory_context, session_id=session_id, emotion_context=emotion_context, time_context=time_context):
                            # 终止检查：WebSocket 路径此前完全没有实现"停止"，
                            # 手机上点了终止也没用
                            if _is_stopped(session_id):
                                stopped = True
                                suffix = "\n\n⏹ [已手动终止]"
                                full_content += suffix
                                await connection_manager.send_stream_chunk(
                                    connection_id, suffix, True,
                                    tokens=tokens, response_ms=response_ms,
                                )
                                return

                            # FIX: done chunk 也可能包含内容（如错误消息），不能丢弃
                            if chunk.get("content"):
                                full_content += chunk["content"]
                                await connection_manager.send_stream_chunk(
                                    connection_id, chunk["content"], False,
                                )
                            if chunk.get("done"):
                                tokens = chunk.get("total_tokens", 0)
                                response_ms = chunk.get("response_time_ms", 0)
                                await connection_manager.send_stream_chunk(
                                    connection_id, "", True,
                                    tokens=tokens, response_ms=response_ms,
                                )

                    await asyncio.wait_for(_stream_reply(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    logger.error("chat", f"WebSocket 聊天超时 ({timeout_s}s)")
                    await connection_manager.send_stream_chunk(
                        connection_id, "\n\n[错误] 回复超时，请重试", True,
                    )
                except Exception as e:
                    logger.error("chat", f"WebSocket 聊天异常: {str(e)}")
                    await connection_manager.send_stream_chunk(
                        connection_id, f"\n\n[错误] {str(e)[:100]}", True,
                    )

                if full_content:
                    _finish_turn(session_id, device_id, user_message,
                                 full_content, tokens, response_ms)

            elif msg_type == "get_history":
                # 获取历史消息
                messages = db.get_messages(session_id, limit=data.get("limit", 50))
                await connection_manager.send_to(connection_id, {
                    "type": "history",
                    "messages": messages,
                })

    except WebSocketDisconnect:
        await connection_manager.disconnect(connection_id)
        logger.connection_log("disconnected", device_id, device_name, "wifi")
    except Exception as e:
        await connection_manager.disconnect(connection_id)
        logger.error("connection", f"WebSocket 错误: {str(e)}")
        logger.connection_log("error", device_id, device_name, "wifi", str(e))


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """
    WebSocket 日志观察端点
    监控面板通过此端点实时接收日志
    """
    await connection_manager.add_log_observer(websocket)
    try:
        while True:
            # 保持连接，接收心跳
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await connection_manager.remove_log_observer(websocket)
    except Exception:
        await connection_manager.remove_log_observer(websocket)


# ===== 记忆系统 API =====

@app.get("/api/memory/stats")
async def get_memory_stats():
    """获取记忆库统计"""
    return memory_manager.get_stats()


@app.get("/api/memory/search")
async def search_memory(q: str, n: int = 5):
    """语义搜索记忆"""
    results = memory_manager.search_memory(q, n_results=n)
    return {"results": results}


@app.post("/api/memory/fact")
async def add_memory_fact(fact: str, session_id: str = ""):
    """手动添加用户事实"""
    memory_manager.add_fact(fact, source="manual", session_id=session_id)
    return {"status": "ok"}


@app.post("/api/memory/summary")
async def add_memory_summary(summary: str, session_id: str = ""):
    """添加对话摘要"""
    memory_manager.add_summary(summary, session_id)
    return {"status": "ok"}


@app.post("/api/memory/clear")
async def clear_memory(collection: str = "all"):
    """清空记忆"""
    if collection == "all":
        memory_manager.clear_all()
    else:
        memory_manager.clear_collection(collection)
    return {"status": "ok"}


# ===== 用户学习引擎 API =====

@app.get("/api/learning/profile")
async def get_user_profile():
    """获取用户画像"""
    profile = learning_engine.profile
    return {
        "basic_info": {
            "name": profile.name,
            "gender": profile.gender,
            "age_range": profile.age_range,
            "location": profile.location,
            "occupation": profile.occupation,
        },
        "personality": profile.personality,
        "communication_style": profile.communication_style,
        "preferences": profile.preferences,
        "emotional_state": {
            "dominant_emotion": profile.emotional_state.get("dominant_emotion", "neutral"),
            "recent_sentiment": profile.emotional_state.get("recent_sentiment", 0.5),
        },
        "activity_patterns": {
            "total_messages": profile.activity_patterns.get("total_messages", 0),
            "active_hours": profile.activity_patterns.get("active_hours", {}),
        },
    }


@app.get("/api/learning/progress")
async def get_learning_progress():
    """获取学习进度"""
    return learning_engine.get_learning_progress()


@app.get("/api/learning/network")
async def get_network_stats():
    """获取记忆神经网络统计"""
    return learning_engine.get_network_stats()


@app.get("/api/learning/context")
async def get_personalized_context(q: str = ""):
    """获取个性化上下文（用于调试）"""
    context = learning_engine.get_personalized_context(current_message=q)
    return {"context": context}


@app.get("/api/emotion/analyze")
async def analyze_emotion(q: str):
    """分析单条文本的情感（用于 API 调用）"""
    engine = get_emotion_engine()
    result = engine.analyze(q)
    return {
        "dominant": result.dominant,
        "emoji": result.emoji,
        "intensity": result.intensity,
        "valence": result.valence,
        "summary": result.summary,
        "instruction": engine.get_adaptive_instruction(result),
    }


@app.get("/api/emotion/trend/{session_id}")
async def get_emotion_trend(session_id: str, window: int = 10):
    """获取某会话的情绪趋势"""
    tracker = get_mood_tracker(session_id)
    trend_text = tracker.get_trend_instruction(window=window)
    records = tracker.get_records(limit=window)
    return {
        "session_id": session_id,
        "records": [r.to_dict() for r in records],
        "trend_instruction": trend_text,
    }


@app.post("/api/emotion/reset")
async def reset_emotion_trend(session_id: str = ""):
    """重置情绪追踪器"""
    tracker = get_mood_tracker(session_id)
    tracker.reset()
    return {"status": "ok", "message": "情绪趋势已重置"}


@app.get("/api/learning/summary")
async def get_user_summary():
    """获取用户摘要"""
    return {"summary": learning_engine.get_user_summary()}


@app.post("/api/learning/reset")
async def reset_learning():
    """重置用户学习数据"""
    learning_engine.reset_profile()
    logger.info("learning", "用户学习数据已重置")
    return {"status": "ok", "message": "用户学习数据已重置"}


# ===== 模型信息 API =====

@app.get("/api/model/info")
async def get_model_info():
    """获取当前模型配置和状态信息"""
    import config
    status = await ollama_client.check_status()
    # 运行时配置优先（否则这里显示的永远是启动时的值）
    model_source = runtime("model_source", MODEL_SOURCE)
    model_info = {
        "model_name": config.OLLAMA_MODEL,
        "model_source": model_source,
        "ollama_status": status.get("status", "unknown"),
        "ollama_models": status.get("models", []),
        "model_available": status.get("model_available", False),
        "generation_options": {
            # GENERATION_OPTIONS / MAX_OUTPUT_CHARS 是 ollama_client 模块级变量，
            # 不能通过 ollama_client（实例）去访问，否则 AttributeError
            "temperature": GENERATION_OPTIONS.get("temperature", 0.7),
            "top_p": GENERATION_OPTIONS.get("top_p", 0.9),
            "num_ctx": GENERATION_OPTIONS.get("num_ctx"),
            "max_output_chars": MAX_OUTPUT_CHARS,
        },
        "server_host": config.SERVER_HOST,
        "server_port": config.SERVER_PORT,
    }
    if model_source == "cloud":
        # CLOUD_PROVIDERS 定义在 cloud_client 里，config 模块并没有这个变量
        model_info["cloud_providers"] = [p["name"] for p in CLOUD_PROVIDERS.values()]
    return model_info


# ===== 健康检查增强 API =====

@app.get("/api/health/detailed")
async def health_detailed():
    """增强版健康检查：包含系统资源使用、连接数、记忆库状态等"""
    import os

    # psutil 是可选依赖：没装时不应让整个接口 500，而是降级返回
    resource_info = {"available": False}
    try:
        import psutil
        process = psutil.Process(os.getpid())
        resource_info = {
            "available": True,
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
            "cpu_percent": round(process.cpu_percent(interval=0.1), 1),
        }
    except ImportError:
        resource_info = {"available": False, "hint": "安装 psutil 可查看内存/CPU：pip install psutil"}

    # 连接统计
    conn_stats = connection_manager.get_stats()

    # 记忆库统计
    memory_stats = memory_manager.get_stats()

    # 模型状态
    ollama_status = await ollama_client.check_status()

    # 活跃会话数（直接 COUNT，不要把所有会话捞出来数长度）
    sessions = db.get_all_sessions(limit=1000)
    active_sessions = len(sessions)

    return {
        "status": "healthy",
        "server": {
            # 服务自身的运行时长（原实现用 psutil.boot_time() 算的是"系统开机时长"，
            # 字段名叫 uptime_seconds 却和本服务无关，误导排查）
            "uptime_seconds": int(time.time() - SERVICE_START_TS),
            "started_at": datetime.fromtimestamp(SERVICE_START_TS).isoformat(),
            **resource_info,
        },
        "connections": conn_stats,
        "memory": memory_stats,
        "model": {
            "source": ollama_status.get("status", "unknown"),
            "available": ollama_status.get("model_available", False),
        },
        "sessions": {
            "active": active_sessions,
        },
        "timestamp": datetime.now().isoformat(),
    }


# ===== 记忆系统管理 API =====

@app.post("/api/memory/auto-cleanup")
async def trigger_auto_cleanup():
    """手动触发记忆库自动清理"""
    result = memory_manager.auto_cleanup()
    return {"status": "ok", "message": "记忆库清理已执行", "result": result}


@app.get("/api/memory/export")
async def export_memory(collection: str = "all"):
    """导出记忆库数据（用于备份）"""
    export_data = {}
    collections = ["conversations", "facts", "summaries"] if collection == "all" else [collection]
    
    for coll in collections:
        try:
            # 用 list_all 而不是 search(coll, "")：
            # 空查询在 TF-IDF 后端会得到全零相似度并被阈值过滤掉，
            # 导致导出结果永远是空的。
            items = memory_manager.list_all(coll)
            # 清理敏感信息
            cleaned = [{
                "id": item["id"],
                "content": item["content"],
                "metadata": item.get("metadata", {}),
            } for item in items]
            export_data[coll] = {
                "count": len(cleaned),
                "items": cleaned,
            }
        except Exception as e:
            export_data[coll] = {"error": str(e)}
    
    return {
        "export_time": datetime.now().isoformat(),
        "backend": memory_manager.backend.get_name(),
        "collections": export_data,
    }


@app.post("/api/memory/import")
async def import_memory(data: dict):
    """导入记忆库数据（用于恢复备份）"""
    try:
        collections = data.get("collections", {})
        imported = {"conversations": 0, "facts": 0, "summaries": 0}
        
        for coll_name, coll_data in collections.items():
            if coll_name not in imported:
                continue
            items = coll_data.get("items", [])
            for item in items:
                try:
                    memory_manager.backend.add_document(
                        coll_name,
                        item["id"],
                        item["content"],
                        item.get("metadata", {})
                    )
                    imported[coll_name] += 1
                except Exception as e:
                    print(f"[Memory] 导入失败: {e}")
        
        return {
            "status": "ok",
            "imported": imported,
            "total": sum(imported.values()),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"导入失败: {str(e)}")


# ===== 会话管理 API =====

@app.get("/api/session/stats")
async def get_session_stats():
    """获取会话统计信息"""
    sessions = db.get_all_sessions(limit=1000)
    now = datetime.now()

    stats = {
        "total_sessions": len(sessions),
        "active_today": 0,
        "total_messages": 0,
        "devices": {},
    }

    for s in sessions:
        # 统计设备
        device = s.get("device_name", "unknown")
        stats["devices"][device] = stats["devices"].get(device, 0) + 1

        # 检查是否今天活跃
        try:
            created = datetime.fromisoformat(s.get("created_at", "")) if s.get("created_at") else None
            if created and created.date() == now.date():
                stats["active_today"] += 1
        except (ValueError, TypeError):
            pass

    # 消息总数直接用 COUNT 聚合。
    # 原实现是「每个会话再查一次消息并数长度」，既慢（N+1）又错
    # （limit=100 会把消息数超过 100 的会话截断）。
    stats["total_messages"] = db.get_message_total()

    # 云端连接统计
    cloud_registered = db.get_registered_devices() if hasattr(db, "get_registered_devices") else []
    stats["cloud_registered_devices"] = len(cloud_registered)

    return stats


@app.get("/api/session/list")
async def list_sessions_overview(limit: int = 20):
    """获取会话列表（含消息数与最后一条消息预览）

    名称改为 list_sessions_overview：原实现与 /api/sessions 的处理函数重名
    （都叫 list_sessions），模块级名字被覆盖，OpenAPI 的 operationId 会冲突。
    """
    result = db.get_session_overview(limit)
    return {"sessions": result, "total": len(result)}


# ===== 系统提示词 API =====

@app.get("/api/prompt/current")
async def get_current_prompt():
    """获取当前使用的系统提示词"""
    from elysia_prompt import ELYSIA_SYSTEM_PROMPT
    return {
        "prompt": ELYSIA_SYSTEM_PROMPT,
        "length": len(ELYSIA_SYSTEM_PROMPT),
    }


# ===== 挂载静态文件 =====
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ===== 监控面板页面 =====

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """管理面板页面 - 重定向到静态文件"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")


# ===== 启动 =====

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info",
    )
