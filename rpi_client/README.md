# 爱莉希雅 AI - 树莓派客户端

通过 WebSocket 连接 PC 端服务器的聊天客户端。

## 文件说明

| 文件 | 说明 |
|------|------|
| `elysia_client.py` | 图形界面客户端（需要显示器） |
| `elysia_cli.py` | 终端命令行客户端（支持 SSH） |
| `setup.py` | Python 包安装脚本 |
| `install.sh` | 一键安装脚本（含桌面快捷方式） |
| `requirements.txt` | Python 依赖 |
| `README.md` | 本文档 |

## 一键安装（推荐）

将 `rpi_client` 文件夹传到树莓派后执行：

```bash
cd /home/pi/rpi_client
chmod +x install.sh
./install.sh
```

安装完成后会自动：
1. 创建 Python 虚拟环境（`venv/`）
2. 在虚拟环境中安装依赖
3. 生成启动脚本（`start_gui.sh`、`start_cli.sh`）
4. 在桌面和应用菜单创建快捷方式

## 安装后使用

**图形界面模式**（需要显示器或 VNC）：
```bash
./start_gui.sh
```

**终端命令行模式**（SSH 或终端）：
```bash
./start_cli.sh
```

或在树莓派的桌面/应用菜单中点击 **「Elysia AI」** 启动。

## 不安装直接运行

```bash
# 创建虚拟环境并安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 图形界面版
python3 elysia_client.py

# 终端命令行版
python3 elysia_cli.py
```

## 使用前提

- 树莓派已安装 Raspberry Pi OS（Python 3.7+）
- PC 端服务器已启动（`python server/main.py`）
- 树莓派和 PC 在同一局域网
- 图形界面版需要连接显示器（HDMI）或使用 VNC
- 命令行版支持 SSH 远程连接