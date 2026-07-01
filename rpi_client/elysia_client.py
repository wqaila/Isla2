"""
爱莉希雅 AI - 树莓派桌面客户端 (优化版)
通过 WebSocket 连接 PC 服务器进行聊天
运行: python3 elysia_client.py
依赖: pip3 install websocket-client
"""
import tkinter as tk
from tkinter import ttk
import threading
import json
import time
import websocket
from datetime import datetime

DEFAULT_SERVER_IP = "10.1.41.114"
DEFAULT_SERVER_PORT = 8080


class ElysiaClient:
    def __init__(self):
        self.ws = None
        self.session_id = None
        self.connected = False
        self.streaming = False
        self.stream_start_time = 0
        self.auto_scroll = True  # 自动滚动开关
        self.total_tokens = 0
        self.msg_count = 0

        self.root = tk.Tk()
        self.root.title("Elysia AI")

        # ---- 归一化 DPI 缩放 ----
        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass

        # ---- 自适应窗口大小 ----
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = max(420, min(int(screen_w * 0.45), 680))
        win_h = max(500, min(int(screen_h * 0.58), 850))
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(360, 420)

        # ---- 主题颜色 ----
        self.BG_MAIN = "#0f0e17"       # 主背景
        self.BG_SIDEBAR = "#1a1932"    # 侧栏/顶栏背景
        self.BG_CHAT = "#13121f"       # 聊天区背景
        self.BG_BUBBLE_USER = "#3d2e6b"  # 用户气泡
        self.BG_BUBBLE_BOT = "#1e1d3a"   # AI 气泡
        self.BG_ENTRY = "#1a1932"      # 输入框背景
        self.FG_PRIMARY = "#e2e0f0"    # 主文字
        self.FG_SECONDARY = "#9b97b8"  # 次要文字
        self.FG_MUTED = "#666380"      # 弱化文字
        self.ACCENT_PINK = "#e8739f"   # 爱莉希雅粉
        self.ACCENT_PINK_DARK = "#c7567f"
        self.ACCENT_BLUE = "#7b8cff"   # 用户蓝色
        self.ACCENT_GREEN = "#5cdb8b"  # 在线绿
        self.ACCENT_RED = "#e85555"    # 离线红
        self.BORDER = "#2a2845"        # 边框色

        self.FONT_FAMILY = "DejaVu Sans"

        self.root.configure(bg=self.BG_MAIN)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._bind_keys()

    def _bind_keys(self):
        """绑定键盘快捷键"""
        self.root.bind("<Control-Return>", self.send_message)
        self.root.bind("<Control_L><Return>", self.send_message)
        self.root.bind("<Escape>", lambda e: self.root.focus_set())

    # ================================================================
    #  UI 构建
    # ================================================================
    def _build_ui(self):
        # ---- 顶部标题栏 ----
        self._build_header()

        # ---- 主聊天区域 (Canvas + 可滚动 Frame) ----
        self._build_chat_area()

        # ---- 底部状态栏 ----
        self._build_status_bar()

        # ---- 底部输入区域 ----
        self._build_input_area()

        # ---- 初始欢迎消息 ----
        self._add_welcome_message()

    def _build_header(self):
        """顶部标题栏：Logo + 连接状态 + 设置按钮"""
        header = tk.Frame(self.root, bg=self.BG_SIDEBAR, height=44)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=self.BG_SIDEBAR)
        left.pack(side="left", padx=(12, 0))

        # Logo 标签
        tk.Label(left, text="✦", font=(self.FONT_FAMILY, 16),
                 fg=self.ACCENT_PINK, bg=self.BG_SIDEBAR).pack(side="left")
        tk.Label(left, text="Elysia AI", font=(self.FONT_FAMILY, 12, "bold"),
                 fg=self.FG_PRIMARY, bg=self.BG_SIDEBAR).pack(side="left", padx=(6, 0))

        # 连接状态指示点
        self.status_dot = tk.Canvas(header, width=10, height=10, bg=self.BG_SIDEBAR,
                                     highlightthickness=0)
        self.status_dot.pack(side="left", padx=(16, 4))
        self._dot = self.status_dot.create_oval(1, 1, 9, 9, fill=self.ACCENT_RED, outline="")

        self.status_label = tk.Label(header, text="离线", font=(self.FONT_FAMILY, 8),
                                      fg=self.ACCENT_RED, bg=self.BG_SIDEBAR)
        self.status_label.pack(side="left")

        # 右侧：设置齿轮按钮
        right = tk.Frame(header, bg=self.BG_SIDEBAR)
        right.pack(side="right", padx=(0, 8))

        self.settings_btn = tk.Label(right, text="⚙", font=(self.FONT_FAMILY, 14),
                                      fg=self.FG_MUTED, bg=self.BG_SIDEBAR,
                                      cursor="hand2")
        self.settings_btn.pack(side="right", padx=4)
        self.settings_btn.bind("<Button-1>", self._toggle_settings)

        # ---- 可折叠的设置面板 (默认隐藏) ----
        self.settings_panel = tk.Frame(self.root, bg=self.BG_SIDEBAR)
        self.settings_visible = False

        # 连接行 1: IP
        r1 = tk.Frame(self.settings_panel, bg=self.BG_SIDEBAR)
        r1.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(r1, text="服务器 IP", font=(self.FONT_FAMILY, 9),
                 fg=self.FG_SECONDARY, bg=self.BG_SIDEBAR, width=8, anchor="w").pack(side="left")
        self.ip_var = tk.StringVar(value=DEFAULT_SERVER_IP)
        ip_entry = tk.Entry(r1, textvariable=self.ip_var, width=18,
                            bg=self.BG_ENTRY, fg=self.FG_PRIMARY,
                            font=(self.FONT_FAMILY, 10), relief="flat",
                            insertbackground=self.ACCENT_PINK)
        ip_entry.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # 连接行 2: 端口
        r2 = tk.Frame(self.settings_panel, bg=self.BG_SIDEBAR)
        r2.pack(fill="x", padx=14, pady=4)
        tk.Label(r2, text="端口", font=(self.FONT_FAMILY, 9),
                 fg=self.FG_SECONDARY, bg=self.BG_SIDEBAR, width=8, anchor="w").pack(side="left")
        self.port_var = tk.StringVar(value=str(DEFAULT_SERVER_PORT))
        port_entry = tk.Entry(r2, textvariable=self.port_var, width=6,
                              bg=self.BG_ENTRY, fg=self.FG_PRIMARY,
                              font=(self.FONT_FAMILY, 10), relief="flat",
                              insertbackground=self.ACCENT_PINK)
        port_entry.pack(side="left", padx=(8, 0))

        # 连接按钮
        r3 = tk.Frame(self.settings_panel, bg=self.BG_SIDEBAR)
        r3.pack(fill="x", padx=14, pady=(8, 10))
        self.connect_btn = tk.Button(r3, text="🔗  连接服务器", command=self.toggle_connection,
                                      bg=self.ACCENT_PINK, fg="#fff",
                                      font=(self.FONT_FAMILY, 10, "bold"),
                                      relief="flat", padx=16, pady=4,
                                      activebackground=self.ACCENT_PINK_DARK,
                                      activeforeground="#fff",
                                      cursor="hand2")
        self.connect_btn.pack()

        # 提示文字
        r4 = tk.Frame(self.settings_panel, bg=self.BG_SIDEBAR)
        r4.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(r4, text="首次使用请先配置服务器地址",
                 font=(self.FONT_FAMILY, 8), fg=self.FG_MUTED,
                 bg=self.BG_SIDEBAR).pack()

    def _build_chat_area(self):
        """聊天区域：用 Canvas + Frame 实现可滚动消息列表"""
        self.chat_container = tk.Frame(self.root, bg=self.BG_CHAT)
        self.chat_container.pack(fill="both", expand=True, side="top")

        # Canvas 作为滚动容器
        self.chat_canvas = tk.Canvas(self.chat_container, bg=self.BG_CHAT,
                                      highlightthickness=0, bd=0)
        self.chat_canvas.pack(side="left", fill="both", expand=True)

        # 滚动条
        self.chat_scrollbar = tk.Scrollbar(self.chat_container, orient="vertical",
                                            command=self._on_scroll)
        self.chat_scrollbar.pack(side="right", fill="y")

        # 消息 Frame (放在 Canvas 内部)
        self.msg_frame = tk.Frame(self.chat_canvas, bg=self.BG_CHAT)
        self.msg_frame_id = self.chat_canvas.create_window((0, 0), window=self.msg_frame,
                                                            anchor="nw", tags="msg_frame")

        # 绑定事件
        self.msg_frame.bind("<Configure>", self._on_frame_configure)
        self.chat_canvas.bind("<Configure>", self._on_canvas_configure)
        self.chat_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.chat_canvas.bind("<MouseWheel>", self._on_mousewheel)

        # 监听滚动位置，判定是否开启自动滚动
        self.chat_scrollbar.bind("<ButtonPress-1>", lambda e: setattr(self, "auto_scroll", False))
        self.chat_scrollbar.bind("<ButtonRelease-1>", self._check_auto_scroll)

    def _build_status_bar(self):
        """底部状态栏：token 计数、响应时间、在线状态"""
        self.status_bar = tk.Frame(self.root, bg=self.BG_SIDEBAR, height=26)
        self.status_bar.pack(fill="x", side="bottom")
        self.status_bar.pack_propagate(False)

        # 左侧：连接提示
        self.status_bar_left = tk.Label(self.status_bar, text="",
                                         font=(self.FONT_FAMILY, 8),
                                         fg=self.FG_MUTED, bg=self.BG_SIDEBAR,
                                         anchor="w")
        self.status_bar_left.pack(side="left", padx=12, fill="x", expand=True)

        # 右侧：统计信息
        self.stats_label = tk.Label(self.status_bar, text="",
                                     font=(self.FONT_FAMILY, 8),
                                     fg=self.FG_MUTED, bg=self.BG_SIDEBAR,
                                     anchor="e")
        self.stats_label.pack(side="right", padx=12)

    def _build_input_area(self):
        """底部输入区域：圆角输入框 + 发送按钮 + 自动滚动切换"""
        input_frame = tk.Frame(self.root, bg=self.BG_SIDEBAR)
        input_frame.pack(fill="x", side="bottom", padx=10, pady=(6, 10))

        # 输入框容器 (模拟圆角)
        entry_container = tk.Frame(input_frame, bg=self.BG_ENTRY, bd=0)
        entry_container.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.input_entry = tk.Text(entry_container, bg=self.BG_ENTRY, fg=self.FG_PRIMARY,
                                    insertbackground=self.ACCENT_PINK,
                                    font=(self.FONT_FAMILY, 11), relief="flat",
                                    wrap="word", height=2, padx=10, pady=6,
                                    bd=0)
        self.input_entry.pack(fill="x", expand=True)
        self.input_entry.bind("<Return>", self._on_return)
        self.input_entry.bind("<Shift-Return>", lambda e: None)  # Shift+Enter 换行
        self.input_entry.configure(state="disabled")

        # 占位提示 (Placeholder 效果)
        self._placeholder = "输入消息..."
        self.input_entry.bind("<FocusIn>", self._on_focus_in)
        self.input_entry.bind("<FocusOut>", self._on_focus_out)
        self._placeholder_active = True

        # 发送按钮
        self.send_btn = tk.Button(input_frame, text="发送",
                                   command=self.send_message,
                                   bg=self.ACCENT_PINK, fg="#fff",
                                   font=(self.FONT_FAMILY, 10, "bold"),
                                   relief="flat", padx=14, pady=4,
                                   activebackground=self.ACCENT_PINK_DARK,
                                   activeforeground="#fff",
                                   cursor="hand2")
        self.send_btn.pack(side="right")
        self.send_btn.configure(state="disabled")

        # 自动滚动切换
        self.auto_scroll_var = tk.BooleanVar(value=True)
        self.auto_scroll_cb = tk.Checkbutton(input_frame, text="↓",
                                              variable=self.auto_scroll_var,
                                              command=self._toggle_auto_scroll,
                                              bg=self.BG_SIDEBAR, fg=self.FG_MUTED,
                                              selectcolor=self.BG_SIDEBAR,
                                              activebackground=self.BG_SIDEBAR,
                                              activeforeground=self.ACCENT_PINK,
                                              font=(self.FONT_FAMILY, 9),
                                              relief="flat")
        self.auto_scroll_cb.pack(side="right", padx=(0, 4))

    # ================================================================
    #  气泡消息
    # ================================================================
    def _add_welcome_message(self):
        """初始欢迎消息气泡"""
        welcome_text = (
            "欢迎来到爱莉希雅 AI 聊天室~ ✨\n"
            "点击左上角 ⚙ 配置服务器地址，然后连接即可开始聊天。"
        )
        self._add_bubble("Elysia", welcome_text, is_user=False, is_system=False)

    def _add_bubble(self, sender, message, is_user, is_system=False):
        """添加聊天气泡到消息列表"""
        container = tk.Frame(self.msg_frame, bg=self.BG_CHAT)
        container.pack(fill="x", padx=10, pady=(4, 2))

        # 对齐：用户右对齐，机器人左对齐
        anchor = "e" if is_user else "w"
        container_inner = tk.Frame(container, bg=self.BG_CHAT)
        container_inner.pack(anchor=anchor, fill="x" if not is_user else None)

        # 气泡颜色
        if is_system:
            bubble_bg = self.BG_SIDEBAR
            text_fg = self.FG_MUTED
            name_fg = self.FG_SECONDARY
        elif is_user:
            bubble_bg = self.BG_BUBBLE_USER
            text_fg = self.FG_PRIMARY
            name_fg = self.ACCENT_BLUE
        else:
            bubble_bg = self.BG_BUBBLE_BOT
            text_fg = "#e8d5f5"
            name_fg = self.ACCENT_PINK

        # 气泡框架
        bubble = tk.Frame(container_inner, bg=bubble_bg, padx=12, pady=8,
                          highlightthickness=1 if is_system else 0,
                          highlightbackground=self.BORDER if is_system else bubble_bg)
        bubble.pack(anchor=anchor)

        # 发送者名称
        tk.Label(bubble, text=sender, font=(self.FONT_FAMILY, 9, "bold"),
                 fg=name_fg, bg=bubble_bg, anchor="w").pack(anchor="w")

        # 消息内容
        msg_label = tk.Label(bubble, text=message, font=(self.FONT_FAMILY, 11),
                             fg=text_fg, bg=bubble_bg, wraplength=420,
                             justify="left", anchor="w")
        msg_label.pack(anchor="w", pady=(4, 0))

        # 时间戳
        if not is_system:
            ts = datetime.now().strftime("%H:%M")
            tk.Label(bubble, text=ts, font=(self.FONT_FAMILY, 7),
                     fg=self.FG_MUTED, bg=bubble_bg,
                     anchor="e" if is_user else "w").pack(
                anchor="e" if is_user else "w", pady=(4, 0))

        if self.auto_scroll:
            self._scroll_to_bottom()

    def _add_system_msg(self, message):
        """添加系统消息"""
        self._add_bubble("系统", message, is_user=False, is_system=True)

    def _scroll_to_bottom(self):
        """滚动到底部"""
        self.chat_canvas.update_idletasks()
        self.chat_canvas.yview_moveto(1.0)

    # ================================================================
    #  流式输出支持
    # ================================================================
    def _add_bot_stream_start(self):
        """开始 AI 流式输出 — 创建一个可更新的气泡"""
        container = tk.Frame(self.msg_frame, bg=self.BG_CHAT)
        container.pack(fill="x", padx=10, pady=(4, 2))

        inner = tk.Frame(container, bg=self.BG_CHAT)
        inner.pack(anchor="w", fill="x")

        bubble = tk.Frame(inner, bg=self.BG_BUBBLE_BOT, padx=12, pady=8)
        bubble.pack(anchor="w")

        tk.Label(bubble, text="Elysia", font=(self.FONT_FAMILY, 9, "bold"),
                 fg=self.ACCENT_PINK, bg=self.BG_BUBBLE_BOT, anchor="w").pack(anchor="w")

        self._stream_msg_label = tk.Label(bubble, text=" ", font=(self.FONT_FAMILY, 11),
                                          fg="#e8d5f5", bg=self.BG_BUBBLE_BOT,
                                          wraplength=420, justify="left", anchor="w")
        self._stream_msg_label.pack(anchor="w", pady=(4, 0))

        # 时间戳占位
        ts = datetime.now().strftime("%H:%M")
        self._stream_ts_label = tk.Label(bubble, text=ts, font=(self.FONT_FAMILY, 7),
                                          fg=self.FG_MUTED, bg=self.BG_BUBBLE_BOT, anchor="w")
        self._stream_ts_label.pack(anchor="w", pady=(4, 0))

        self._stream_bubble = bubble
        self._stream_container = container
        self._stream_inner = inner

        if self.auto_scroll:
            self._scroll_to_bottom()

    def _update_stream(self, content):
        """更新流式输出文字"""
        try:
            current = self._stream_msg_label.cget("text")
            if current == " ":
                self._stream_msg_label.configure(text=content)
            else:
                self._stream_msg_label.configure(text=current + content)
        except Exception:
            pass
        if self.auto_scroll:
            self._scroll_to_bottom()

    def _finish_stream(self, tokens, ms):
        """流式输出完成 — 添加统计"""
        elapsed = int((time.time() - self.stream_start_time) * 1000) if self.stream_start_time else ms
        self.total_tokens += tokens
        self.msg_count += 1

        try:
            meta = ""
            if tokens:
                meta = f"{tokens} tokens · {elapsed}ms"
            elif elapsed:
                meta = f"{elapsed}ms"
            if meta:
                current_ts = self._stream_ts_label.cget("text")
                self._stream_ts_label.configure(text=f"{current_ts} · {meta}")
        except Exception:
            pass

        self._update_stats()
        if self.auto_scroll:
            self._scroll_to_bottom()

    def _update_stats(self):
        """更新底部统计信息"""
        if self.msg_count > 0:
            text = f"消息 {self.msg_count} · Token {self.total_tokens}"
        else:
            text = ""
        self.stats_label.configure(text=text)

    # ================================================================
    #  滚动事件
    # ================================================================
    def _on_frame_configure(self, event):
        """msg_frame 大小变化时更新 Canvas 滚动区"""
        self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Canvas 大小变化时保持 msg_frame 宽度一致"""
        self.chat_canvas.itemconfig(self.msg_frame_id, width=event.width)

    def _on_mousewheel(self, event):
        """鼠标滚轮滚动"""
        if self.chat_scrollbar.get() == (0.0, 1.0):
            return
        self.chat_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _check_auto_scroll(self, event=None):
        """根据滚动条位置判断是否自动滚动"""
        _, y1 = self.chat_scrollbar.get()
        self.auto_scroll = (y1 >= 0.95)
        self.auto_scroll_var.set(self.auto_scroll)

    def _on_scroll(self, *args):
        """滚动条回调"""
        self.chat_canvas.yview(*args)

    def _toggle_auto_scroll(self):
        """切换自动滚动"""
        self.auto_scroll = self.auto_scroll_var.get()

    # ================================================================
    #  输入处理
    # ================================================================
    def _on_return(self, event):
        """Enter 键发送消息 (Shift+Enter 换行)"""
        if not event.state & 0x1:  # 没有按 Shift
            self.send_message()
            return "break"
        return None

    def _on_focus_in(self, event):
        """输入框获得焦点 — 清除 placeholder"""
        if self._placeholder_active:
            self.input_entry.delete("1.0", "end-1c")
            self.input_entry.configure(fg=self.FG_PRIMARY)
            self._placeholder_active = False

    def _on_focus_out(self, event):
        """输入框失去焦点 — 恢复 placeholder"""
        content = self.input_entry.get("1.0", "end-1c").strip()
        if not content:
            self.input_entry.delete("1.0", "end-1c")
            self.input_entry.insert("1.0", self._placeholder)
            self.input_entry.configure(fg=self.FG_MUTED)
            self._placeholder_active = True

    # ================================================================
    #  设置面板
    # ================================================================
    def _toggle_settings(self, event=None):
        """展开/折叠设置面板"""
        if self.settings_visible:
            self.settings_panel.pack_forget()
            self.settings_visible = False
            self.settings_btn.configure(fg=self.FG_MUTED)
        else:
            self.settings_panel.pack(fill="x", side="top",
                                      before=self.chat_container)
            self.settings_visible = True
            self.settings_btn.configure(fg=self.ACCENT_PINK)

    # ================================================================
    #  连接逻辑
    # ================================================================
    def toggle_connection(self):
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        ip = self.ip_var.get().strip()
        port = self.port_var.get().strip()
        if not ip:
            self._add_system_msg("请输入服务器 IP 地址")
            return

        ws_url = f"ws://{ip}:{port}/ws/chat?device_id=rpi&device_name=RPi"
        self._update_connection_status("connecting")

        self.status_bar_left.configure(text=f"正在连接 {ip}:{port}...")
        self._add_system_msg(f"正在连接 {ip}:{port}...")

        def on_open(ws):
            self.connected = True
            self.root.after(0, lambda: self._set_connected(True, ip, port))

        def on_message(ws, message):
            try:
                data = json.loads(message)
                t = data.get("type", "")
                if t == "connected":
                    self.session_id = data.get("session_id")
                elif t == "stream":
                    content = data.get("content", "")
                    done = data.get("done", False)
                    if not done:
                        if not self.streaming:
                            self.streaming = True
                            self.stream_start_time = time.time()
                            self.root.after(0, self._add_bot_stream_start)
                        self.root.after(0, lambda c=content: self._update_stream(c))
                    else:
                        tokens = data.get("tokens", 0)
                        ms = data.get("response_ms", 0)
                        self.streaming = False
                        self.root.after(0, lambda: self._finish_stream(tokens, ms))
                        self.root.after(0, lambda: self._set_input(True))
            except Exception:
                pass

        def on_error(ws, error):
            self.root.after(0, lambda: self._add_system_msg(f"连接错误: {error}"))

        def on_close(ws, code, reason):
            self.connected = False
            self.streaming = False
            self.root.after(0, lambda: self._set_connected(False, ip, port))

        def run_ws():
            try:
                self.ws = websocket.WebSocketApp(ws_url,
                    on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close)
                self.ws.run_forever()
            except Exception as e:
                self.root.after(0, lambda: self._add_system_msg(f"连接失败: {e}"))
                self.root.after(0, lambda: self._set_connected(False, ip, port))

        threading.Thread(target=run_ws, daemon=True).start()

    def _set_connected(self, ok, ip="", port=""):
        if ok:
            self._update_connection_status("connected")
            self.connect_btn.configure(text="🔌  断开连接")
            self._set_input(True)
            self._add_system_msg(f"✅ 已连接到 {ip}:{port}")
            self.status_bar_left.configure(text=f"已连接 {ip}:{port}")
            self.input_entry.focus_set()
            # 自动折叠设置面板
            if self.settings_visible:
                self._toggle_settings()
        else:
            self._update_connection_status("disconnected")
            self.connect_btn.configure(text="🔗  连接服务器")
            self._set_input(False)
            self._add_system_msg("连接已断开")
            self.status_bar_left.configure(text="")
            self.stats_label.configure(text="")

    def _update_connection_status(self, status):
        """更新连接状态指示"""
        if status == "connected":
            self.status_dot.itemconfig(self._dot, fill=self.ACCENT_GREEN)
            self.status_label.configure(text="在线", fg=self.ACCENT_GREEN)
        elif status == "connecting":
            self.status_label.configure(text="连接中...", fg="#f0a040")
            self.status_dot.itemconfig(self._dot, fill="#f0a040")
        else:
            self.status_dot.itemconfig(self._dot, fill=self.ACCENT_RED)
            self.status_label.configure(text="离线", fg=self.ACCENT_RED)

    def _set_input(self, enabled):
        """启用/禁用输入框和发送按钮"""
        state = "normal" if enabled else "disabled"
        self.input_entry.configure(state=state)
        self.send_btn.configure(state=state)
        if enabled:
            self.input_entry.focus_set()

    def disconnect(self):
        if self.ws:
            self.ws.close()
        self.connected = False

    def send_message(self, event=None):
        msg = self.input_entry.get("1.0", "end-1c").strip()
        if not msg or not self.connected:
            return
        if self._placeholder_active:
            return

        self._add_bubble("我", msg, is_user=True)
        self.input_entry.delete("1.0", "end-1c")
        try:
            self.ws.send(json.dumps({"type": "chat", "message": msg}))
        except Exception as e:
            self._add_system_msg(f"发送失败: {e}")

    def on_close(self):
        self.disconnect()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    client = ElysiaClient()
    client.run()

if __name__ == "__main__":
    main()