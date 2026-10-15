#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""B 站 Cookie 设置界面（tkinter 桌面窗口）。

【为什么做这个界面】
在终端里粘贴 Cookie 很别扭：粘贴长字符串容易带上换行、终端可能吞字符、
而且**看不出来到底成没成**（`is_logged_in()` 只查字符串，Cookie 过期照样说「有效」）。

更重要的是**安全**：以前要把 Cookie 贴到别处（聊天、文档）才能求助；
现在粘进本地窗口即可，**内容不经过网络、不落进任何日志**。

【为什么用 tkinter 而不是 Flask 网页】
1. **零新增依赖** —— tkinter 是标准库自带的；
2. **不走网络栈** —— 本地网页也要起服务器、占端口，凭据会经过 localhost；
3. 这是个「粘一次就走」的场景，原生窗口足够。

【设计：逻辑与界面分离】
`CookieEditor` 不依赖 tkinter，负责**真正会出错的逻辑**
（保存前备份、联网校验、状态描述）；`CookieWindow` 只负责
「读输入框 → 调 CookieEditor → 显示返回值」。
这样逻辑层可以在 CI 上测（见 tests/test_cookie_gui_core.py），
而界面层保持极薄。
"""

import os
import shutil
import sys
from datetime import datetime
from typing import Dict, Optional, Tuple

# 允许「直接跑本文件」与「作为包导入」两种方式
try:
    from .cookie_manager import CookieManager
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cookie_manager import CookieManager

__all__ = ["CookieEditor", "CookieWindow", "main", "DEFAULT_COOKIE_PATH"]


DEFAULT_COOKIE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "bilibili_cookies.txt")


class CookieEditor:
    """Cookie 的保存 / 校验 / 状态 / 清除 —— **不依赖任何 GUI 库**。

    界面层只调这四个方法并把返回值显示出来。
    """

    def __init__(self, cookie_path: str = None):
        self.cookie_path = cookie_path or DEFAULT_COOKIE_PATH
        self._manager = CookieManager(cookie_path=self.cookie_path)

    # ------------------------------------------------------------ 内部

    def _check_online(self) -> Tuple[bool, str]:
        """联网校验（测试里会被替换掉，避免真发请求）。"""
        return self._manager.check_cookie_online()

    def cookies(self) -> Dict[str, str]:
        """读回当前已保存的 Cookie（便于断言与展示字段名）。"""
        return self._manager.get_cookie_dict()

    def _backup_if_exists(self) -> Optional[str]:
        """若已有 Cookie 文件，先备份一份带时间戳的副本。

        ⚠️ `CookieManager.set_cookie()` 是**直接覆盖**的，没有备份。
        界面里粘贴很容易出错（粘成半截、粘错内容），没有退路会很痛。

        :return: 备份文件路径；原本没有文件时返回 None
        """
        if not os.path.exists(self.cookie_path):
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{self.cookie_path}.bak-{stamp}"
        try:
            shutil.copy2(self.cookie_path, backup)
            return backup
        except OSError as e:
            print(f"  ⚠️ 备份失败（继续保存）：{e}")
            return None

    @staticmethod
    def _looks_like_guest(cookie_str: str) -> bool:
        """判断像不像**游客 Cookie**（有设备标识但完全没有登录凭据）。"""
        return "SESSDATA" not in cookie_str

    # ------------------------------------------------------------ 公开

    def save(self, raw: str) -> Tuple[bool, str]:
        """保存并**立即联网校验**，返回 ``(是否有效, 给用户看的说明)``。

        与 `CookieManager.set_cookie()` 的差别（都是刻意的）：
        1. **先备份**旧文件，可回退；
        2. **缺 SESSDATA 时明确指出** —— 最常见的手滑就是复制到游客 Cookie
           （一堆 `buvid3`/`_uuid`，没有 SESSDATA），
           保存后以为成功、实际根本没登录；
        3. 保存后**联网校验**并回显用户名，而不是只说一句「已保存」。
        """
        text = (raw or "").strip()
        if not text:
            return False, "Cookie 不能为空，已取消（文件未改动）"

        if self._looks_like_guest(text):
            return False, (
                "这份 Cookie 里**没有 SESSDATA** —— 它是「未登录 / 游客」状态的 Cookie。\n"
                "请先登录 B 站，再从「应用 → Cookie → https://www.bilibili.com」"
                "里复制含 SESSDATA 的那份。\n"
                "（文件未改动）"
            )

        backup = self._backup_if_exists()
        self._manager.set_cookie(text)

        ok_flag, msg = self._check_online()
        if backup:
            msg = f"{msg}\n（旧 Cookie 已备份：{os.path.basename(backup)}）"
        if ok_flag:
            return True, f"✓ {msg}"
        return False, (
            f"✗ 已保存，但服务端不认：{msg}\n"
            "可能原因：复制不全、复制的是未登录状态的 Cookie、或 Cookie 已过期。"
        )

    def status(self) -> str:
        """查看当前状态（**联网校验**，不是只看文件里有没有字符串）。"""
        if not os.path.exists(self.cookie_path):
            return "✗ 尚未设置 Cookie（文件不存在）"
        ok_flag, msg = self._check_online()
        return f"{'✓' if ok_flag else '✗'} {msg}"

    def clear(self) -> str:
        """清除 Cookie（**先备份**，避免误点后无法恢复）。"""
        if not os.path.exists(self.cookie_path):
            return "没有 Cookie 文件，无需清除"
        backup = self._backup_if_exists()
        try:
            os.remove(self.cookie_path)
        except OSError as e:
            return f"✗ 删除失败：{e}"
        if backup:
            return f"✓ 已清除（备份留在 {os.path.basename(backup)}）"
        return "✓ 已清除"


# ==================================================================== 界面层

def _build_window(editor: "CookieEditor"):
    """构造窗口（延迟到调用时才 import tkinter，避免无 GUI 环境导入即崩）。"""
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title("B 站 Cookie 设置 — bilibili_downloader")
    root.geometry("760x560")
    root.minsize(620, 460)

    # ---- 顶部：当前状态 ----
    tk.Label(root, text="当前状态", anchor="w",
             font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", padx=12, pady=(12, 2))
    status_var = tk.StringVar(value="点「查看状态」检查")
    tk.Label(root, textvariable=status_var, anchor="w", justify="left",
             wraplength=720, fg="#333").pack(fill="x", padx=12)

    # ---- 中部：粘贴区 ----
    tk.Label(root, text="把浏览器里复制的 Cookie 粘到下面（至少要含 SESSDATA）",
             anchor="w", font=("Microsoft YaHei UI", 10, "bold")).pack(
        fill="x", padx=12, pady=(12, 2))
    tk.Label(root,
             text="获取方法：F12 → 应用(Application) → 存储 → Cookie → "
                  "https://www.bilibili.com → 复制 SESSDATA / bili_jct / DedeUserID",
             anchor="w", fg="#666", wraplength=720).pack(fill="x", padx=12)

    text = scrolledtext.ScrolledText(root, height=9, wrap="char",
                                     font=("Consolas", 10))
    text.pack(fill="both", expand=True, padx=12, pady=6)

    # ---- 底部：按钮 + 结果 ----
    result_var = tk.StringVar(value="")
    result_label = tk.Label(root, textvariable=result_var, anchor="w",
                            justify="left", wraplength=720, fg="#0a6")

    def _show(ok_flag: bool, msg: str):
        result_var.set(msg)
        result_label.config(fg="#0a6" if ok_flag else "#c00")

    def on_save():
        raw = text.get("1.0", "end")
        ok_flag, msg = editor.save(raw)
        _show(ok_flag, msg)
        if ok_flag:
            status_var.set(msg.splitlines()[0])
            # 成功后清空输入框：凭据不该长时间留在界面上
            text.delete("1.0", "end")
        else:
            messagebox.showwarning("未能生效", msg)

    def on_status():
        msg = editor.status()
        status_var.set(msg)
        _show(msg.startswith("✓"), msg)

    def on_clear():
        if not messagebox.askyesno("确认", "确定要清除已保存的 Cookie 吗？\n"
                                            "（会先自动备份一份）"):
            return
        msg = editor.clear()
        status_var.set(msg)
        _show(msg.startswith("✓"), msg)

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=12, pady=(0, 6))
    tk.Button(bar, text="保存并验证", width=14, command=on_save).pack(side="left")
    tk.Button(bar, text="查看状态", width=12, command=on_status).pack(side="left", padx=6)
    tk.Button(bar, text="清除", width=8, command=on_clear).pack(side="left")

    result_label.pack(fill="x", padx=12, pady=(0, 12))

    # 打开时自动查一次状态，省得用户先点一下
    root.after(200, on_status)
    return root


def main() -> int:
    """启动界面。无 GUI 环境时给出明确提示而不是抛栈。"""
    editor = CookieEditor()
    try:
        root = _build_window(editor)
    except Exception as e:                      # 常见：无显示设备 / tkinter 缺失
        print(f"✗ 无法启动图形界面：{type(e).__name__}: {e}")
        print("\n可改用命令行设置：")
        print("  python -m bilibili_downloader --interactive   # 6 → 1 → 粘贴")
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
