"""
WebSocket 连接管理器
管理所有客户端的 WebSocket 连接
"""
import json
import asyncio
from datetime import datetime
from fastapi import WebSocket
from config import HEARTBEAT_INTERVAL


class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        # 活跃连接: {connection_id: {"ws": WebSocket, "device_id": str, "device_name": str, ...}}
        self.active_connections: dict[str, dict] = {}
        # 日志观察者（监控面板的 WebSocket）
        self.log_observers: list[WebSocket] = []

    async def connect(self, websocket: WebSocket, connection_id: str,
                      device_id: str = "", device_name: str = ""):
        """接受新的 WebSocket 连接"""
        await websocket.accept()
        self.active_connections[connection_id] = {
            "ws": websocket,
            "device_id": device_id,
            "device_name": device_name,
            "connected_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
        }
        # 通知日志观察者
        await self.broadcast_log({
            "type": "connection",
            "event": "connected",
            "device_id": device_id,
            "device_name": device_name,
            "connection_id": connection_id,
            "time": datetime.now().isoformat(),
        })

    async def disconnect(self, connection_id: str):
        """断开连接"""
        conn_info = self.active_connections.pop(connection_id, None)
        if conn_info:
            # FIX #5: 显式关闭底层 WebSocket 连接，不依赖垃圾回收
            ws = conn_info.get("ws")
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
            await self.broadcast_log({
                "type": "connection",
                "event": "disconnected",
                "device_id": conn_info.get("device_id", ""),
                "device_name": conn_info.get("device_name", ""),
                "connection_id": connection_id,
                "time": datetime.now().isoformat(),
            })

    async def send_to(self, connection_id: str, data: dict):
        """向指定连接发送消息"""
        conn_info = self.active_connections.get(connection_id)
        if conn_info:
            ws = conn_info["ws"]
            try:
                await ws.send_json(data)
                conn_info["last_active"] = datetime.now().isoformat()
            except Exception:
                await self.disconnect(connection_id)

    async def send_stream_chunk(self, connection_id: str, content: str,
                                done: bool, **kwargs):
        """发送流式消息块"""
        msg = {
            "type": "stream",
            "content": content,
            "done": done,
            **kwargs,
        }
        await self.send_to(connection_id, msg)

    def get_active_count(self) -> int:
        """获取活跃连接数"""
        return len(self.active_connections)

    def get_active_list(self) -> list:
        """获取所有活跃连接信息"""
        result = []
        for conn_id, info in self.active_connections.items():
            result.append({
                "connection_id": conn_id,
                "device_id": info.get("device_id", ""),
                "device_name": info.get("device_name", ""),
                "connected_at": info.get("connected_at", ""),
                "last_active": info.get("last_active", ""),
            })
        return result

    # ===== 日志观察者（监控面板） =====

    async def add_log_observer(self, websocket: WebSocket):
        """添加日志观察者"""
        await websocket.accept()
        self.log_observers.append(websocket)

    async def remove_log_observer(self, websocket: WebSocket):
        """移除日志观察者"""
        if websocket in self.log_observers:
            self.log_observers.remove(websocket)

    async def broadcast_log(self, log_data: dict):
        """向所有日志观察者广播日志"""
        disconnected = []
        for observer in self.log_observers:
            try:
                await observer.send_json(log_data)
            except Exception:
                disconnected.append(observer)
        for obs in disconnected:
            self.log_observers.remove(obs)


# 全局实例
connection_manager = ConnectionManager()
