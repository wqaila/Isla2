#!/bin/bash
# Elysia AI Client - 一键安装脚本（虚拟环境版）
# 运行: chmod +x install.sh && ./install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo ""
echo "========================================"
echo "   Elysia AI Client - 安装程序"
echo "========================================"
echo ""

echo "[信息] 检测硬件平台..."

# 识别树莓派型号
if [ -f /proc/device-tree/model ]; then
    RPI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
    echo "  → $RPI_MODEL"
else
    RPI_MODEL="Unknown"
    echo "  → 非树莓派设备或无法识别"
fi

# 检查 Python 3
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python 3，请先安装: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# ===== 安装系统依赖 =====
echo "[信息] 安装系统依赖..."

# 安装 tkinter（如果没有）
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "  → 安装 python3-tk..."
    sudo apt update && sudo apt install -y python3-tk
else
    echo "  → python3-tk 已安装"
fi

# 安装 DejaVu 字体（树莓派 Linux 通用字体，避免 Helvetica 缺失）
if ! fc-list | grep -qi "DejaVu Sans"; then
    echo "  → 安装 fonts-dejavu-core..."
    sudo apt install -y fonts-dejavu-core
else
    echo "  → DejaVu 字体已安装"
fi

# ===== RPi 5 / Wayland 兼容性处理 =====
if echo "$RPI_MODEL" | grep -qi "Raspberry Pi 5"; then
    echo "[信息] 检测到树莓派 5，检查 Wayland 兼容性..."
    
    # 检查是否运行在 Wayland 下
    if echo "$XDG_SESSION_TYPE" | grep -qi "wayland"; then
        echo "  → 当前会话: Wayland（Tkinter 需要 X11 兼容层）"
        if ! command -v Xwayland &> /dev/null; then
            echo "  → 安装 xwayland..."
            sudo apt install -y xwayland
        else
            echo "  → xwayland 已安装"
        fi
    else
        echo "  → 当前会话: X11（兼容 Tkinter）"
    fi
fi

cd "$SCRIPT_DIR"

# ===== 创建虚拟环境 =====
if [ ! -d "$VENV_DIR" ]; then
    echo "[信息] 创建虚拟环境: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    echo "[信息] 虚拟环境已存在，跳过创建"
fi

# ===== 激活虚拟环境并安装依赖 =====
echo "[信息] 安装依赖..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo ""
echo "[完成] 依赖安装成功！"
echo ""

# ===== 创建启动脚本 =====

# GUI 启动脚本
cat > "$SCRIPT_DIR/start_gui.sh" << STARTEOF
#!/bin/bash
cd "$SCRIPT_DIR"
source venv/bin/activate
python3 elysia_client.py
deactivate
STARTEOF
chmod +x "$SCRIPT_DIR/start_gui.sh"

# CLI 启动脚本
cat > "$SCRIPT_DIR/start_cli.sh" << STARTEOF
#!/bin/bash
cd "$SCRIPT_DIR"
source venv/bin/activate
python3 elysia_cli.py
deactivate
STARTEOF
chmod +x "$SCRIPT_DIR/start_cli.sh"

echo "[完成] 启动脚本已创建:"
echo "  GUI: ./start_gui.sh"
echo "  CLI: ./start_cli.sh"
echo ""

# ===== 创建桌面快捷方式 =====
DESKTOP_DIR="$HOME/Desktop"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons"

mkdir -p "$APP_DIR" "$ICON_DIR" "$DESKTOP_DIR"

# 生成图标
cat > "$ICON_DIR/elysia-ai.svg" << 'ICONEOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <circle cx="32" cy="32" r="30" fill="#1a1a2e"/>
  <text x="32" y="42" text-anchor="middle" font-size="32" font-family="Arial" fill="#ff69b4">E</text>
</svg>
ICONEOF

# GUI 桌面快捷方式
cat > "$APP_DIR/elysia-ai.desktop" << EOF
[Desktop Entry]
Name=Elysia AI
Comment=Elysia AI Chat Client (GUI)
Exec=$SCRIPT_DIR/start_gui.sh
Icon=$ICON_DIR/elysia-ai.svg
Terminal=false
Type=Application
Categories=Network;Chat;
EOF

# CLI 桌面快捷方式
cat > "$APP_DIR/elysia-ai-cli.desktop" << EOF
[Desktop Entry]
Name=Elysia AI (Terminal)
Comment=Elysia AI Chat Client (CLI)
Exec=lxterminal -e "$SCRIPT_DIR/start_cli.sh"
Icon=$ICON_DIR/elysia-ai.svg
Terminal=false
Type=Application
Categories=Network;Chat;
EOF

# 复制到桌面
cp "$APP_DIR/elysia-ai.desktop" "$DESKTOP_DIR/" 2>/dev/null || true
chmod +x "$DESKTOP_DIR/elysia-ai.desktop" 2>/dev/null || true
cp "$APP_DIR/elysia-ai-cli.desktop" "$DESKTOP_DIR/" 2>/dev/null || true
chmod +x "$DESKTOP_DIR/elysia-ai-cli.desktop" 2>/dev/null || true

echo "[完成] 桌面快捷方式已创建"
echo ""
echo "========================================"
echo "   安装完成！"
echo "========================================"
echo ""
echo "启动方式："
echo "  图形界面: ./start_gui.sh"
echo "  终端模式: ./start_cli.sh"
echo "  桌面菜单: 点击 'Elysia AI'"
echo ""