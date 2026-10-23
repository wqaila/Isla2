"""通过 python -m gateway 启动单进程网关。"""
import uvicorn

from .app import create_app
from .config import load_settings


def main():
    settings = load_settings()
    print(f"zjy11 gateway: http://127.0.0.1:{settings.port}")
    print("设备令牌：使用 GATEWAY_DEVICE_TOKEN，或本项目 .local/device_token；不在控制台输出。")
    print(f"云端配置：{'完整' if settings.configured else '未完成'}；真实调用：{'已开启' if settings.cloud_enabled else '关闭'}")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
