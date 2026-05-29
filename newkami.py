#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
卡密登录 Tkinter原生版
单码登录，去除换绑，自动保存/加载卡密
"""

import sys
import os
import threading
import time
import json
from loguru import logger
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# 导入主程序版本号
try:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ab_video_fusion import APP_VERSION
except ImportError:
    APP_VERSION = "1.0.0"

from fnkuaiyan_go_based import FnKuaiYanGoBasedAPI


class HeartBeatConfig:
    """心跳配置类"""

    def __init__(self):
        self.config_file = "heartbeat_config.json"
        self.default_config = {
            "heartbeat_interval": 30,  # 心跳间隔（秒）
            "max_failures": 3,  # 最大失败次数
            "auto_start": True  # 登录后自动启动心跳
        }
        self.config = self.load_config()

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for key, value in self.default_config.items():
                    if key not in config:
                        config[key] = value
                return config
            return self.default_config.copy()
        except Exception:
            return self.default_config.copy()

    def save_config(self):
        """保存配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            logger.debug(f"配置保存失败: {e}")
            return False

    def get(self, key, default=None):
        """获取配置值"""
        return self.config.get(key, default)

    def set(self, key, value):
        """设置配置值"""
        self.config[key] = value


class LoginApp:
    def __init__(self, root):
        self.root = root
        self.root.title("卡密登录")
        self.root.geometry("400x380")
        self.root.resizable(False, False)

        # 窗口居中
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - self.root.winfo_width()) // 2
        y = (self.root.winfo_screenheight() - self.root.winfo_height()) // 2
        self.root.geometry(f"+{x}+{y}")

        # 内部状态
        self.api = None
        self.app_version = APP_VERSION
        self.machine_code = ""
        self.is_logged_in = False
        self.card_config_file = "saved_card.json"

        # 心跳控制
        self.heartbeat_config = HeartBeatConfig()
        self.hb_running = False
        self.hb_thread = None

        # 变量绑定
        self.card_var = tk.StringVar()

        self.setup_ui()
        self.load_saved_card()

        # 启动初始化线程
        self.start_initialization()

    def setup_ui(self):
        """原生 ttk 界面布局"""
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 公告区 ---
        ttk.Label(main_frame, text="📢 公告", font=("", 10, "bold")).pack(anchor=tk.W, pady=(0, 5))
        self.announcement_text = scrolledtext.ScrolledText(main_frame, height=6, font=("", 10), state=tk.DISABLED,
                                                           wrap=tk.WORD)
        self.announcement_text.pack(fill=tk.X, pady=(0, 15))

        # --- 登录区 ---
        login_frame = ttk.LabelFrame(main_frame, text="🔐 登录", padding=15)
        login_frame.pack(fill=tk.X, pady=(0, 15))

        # 卡密输入
        self.card_entry = ttk.Entry(login_frame, textvariable=self.card_var, font=("", 11), justify="center")
        self.card_entry.pack(fill=tk.X, pady=(0, 10), ipady=5)
        self.card_entry.bind("<Return>", lambda e: self.handle_login())

        # 登录按钮
        self.btn_login = ttk.Button(login_frame, text="登  录", command=self.handle_login)
        self.btn_login.pack(fill=tk.X, ipady=3)

        # 机器码提示
        self.machine_lbl = ttk.Label(login_frame, text="机器码: 获取中...", foreground="gray", font=("", 8))
        self.machine_lbl.pack(anchor=tk.CENTER, pady=(5, 0))

        # --- 状态栏 ---
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.version_lbl = ttk.Label(status_frame, text=f"v{self.app_version}", foreground="gray")
        self.version_lbl.pack(side=tk.LEFT)

        self.hb_lbl = ttk.Label(status_frame, text="💤", foreground="gray")
        self.hb_lbl.pack(side=tk.LEFT, padx=10)

        self.status_lbl = ttk.Label(status_frame, text="● 连接中...", foreground="orange")
        self.status_lbl.pack(side=tk.RIGHT)

    # ---------------------------
    # 配置加载与保存
    # ---------------------------
    def load_saved_card(self):
        """加载保存的卡密"""
        try:
            if os.path.exists(self.card_config_file):
                with open(self.card_config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.card_var.set(data.get("card_number", ""))
        except Exception as e:
            logger.debug(f"卡密加载失败: {e}")

    def save_card(self):
        """保存卡密"""
        try:
            with open(self.card_config_file, 'w', encoding='utf-8') as f:
                json.dump({"card_number": self.card_var.get().strip()}, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"卡密保存失败: {e}")

    # ---------------------------
    # 核心业务逻辑 (多线程)
    # ---------------------------
    def set_announcement(self, text):
        self.announcement_text.config(state=tk.NORMAL)
        self.announcement_text.delete(1.0, tk.END)
        self.announcement_text.insert(tk.END, text)
        self.announcement_text.config(state=tk.DISABLED)

    def start_initialization(self):
        """后台初始化 API"""

        def init_task():
            try:
                self.api = FnKuaiYanGoBasedAPI()
                token_result = self.api.get_token()
                if token_result == "ok":
                    self.machine_code = self.api.get_machine_code()
                    announcement = self.api.get_announcement()
                    if isinstance(announcement, str):
                        announcement = announcement.replace('#', '\n').replace('\\n', '\n')

                    # 切换回主线程更新UI
                    self.root.after(0, self.on_init_success, announcement)
                else:
                    self.root.after(0, self.on_init_failed, token_result)
            except Exception as e:
                self.root.after(0, self.on_init_failed, str(e))

        threading.Thread(target=init_task, daemon=True).start()

    def on_init_success(self, announcement):
        self.status_lbl.config(text="● 在线", foreground="green")
        mc = self.machine_code
        self.machine_lbl.config(text=f"设备: {mc[:6]}...{mc[-3:]}")
        self.set_announcement(announcement if "失败" not in announcement else "暂无公告内容")

    def on_init_failed(self, msg):
        self.status_lbl.config(text="● 离线", foreground="red")
        self.machine_lbl.config(text="初始化失败")
        self.set_announcement(f"初始化失败:\n{msg}")

    def handle_login(self):
        """处理登录请求"""
        card_number = self.card_var.get().strip()
        if not card_number:
            messagebox.showwarning("提示", "请输入卡密！")
            self.card_entry.focus()
            return

        if not self.api:
            messagebox.showerror("错误", "系统尚未初始化完成，请稍候。")
            return

        self.btn_login.config(state=tk.DISABLED, text="登录中...")

        def login_task():
            try:
                # 再次校验Token
                if not self.api.token:
                    if self.api.get_token() != "ok":
                        self.root.after(0, self.on_login_finish, False, "Token获取失败")
                        return

                result = self.api.card_login(card_number, self.app_version)
                self.root.after(0, self.on_login_finish, result.startswith("ok|"), result)
            except Exception as e:
                self.root.after(0, self.on_login_finish, False, str(e))

        threading.Thread(target=login_task, daemon=True).start()

    def on_login_finish(self, success, msg):
        self.btn_login.config(state=tk.NORMAL, text="登  录")

        if success:
            self.is_logged_in = True
            self.save_card()  # 登录成功立刻保存一次
            expiry_time = msg.split('|')[1]

            # 将api实例挂载到全局(兼容老系统)
            import fnkuaiyan_go_based
            from newtkmain import AppGUI
            fnkuaiyan_go_based.api = self.api
            # 1. 隐藏登录窗口
            self.root.withdraw()

            # 3. 弹窗提示并打开你的主窗口
            messagebox.showinfo("登录成功", f"✅ 登录成功！\n\n到期时间: {expiry_time}")
            AppGUI(self.root, self.on_closing)
            self.start_heartbeat()
        else:
            messagebox.showerror("登录失败", f"❌ 登录失败\n\n{msg}")

    # 心跳维持与退出逻辑
    # ---------------------------
    def start_heartbeat(self):
        if not self.heartbeat_config.get("auto_start", True):
            return

        self.stop_heartbeat()
        self.hb_running = True

        interval = self.heartbeat_config.get("heartbeat_interval", 30)
        # 获取最大容错次数，默认 3 次
        max_failures = self.heartbeat_config.get("max_failures", 3)
        self.hb_lbl.config(text="💓", foreground="blue")

        def hb_task():
            failure_count = 0  # 初始化失败计数器

            while self.hb_running:
                # 切片睡眠，防止阻塞主线程退出
                for _ in range(interval):
                    if not self.hb_running: return
                    time.sleep(1)

                if not self.hb_running: break

                try:
                    res = self.api.heartbeat()
                    # 只要是正常状态(status == 1)，就判定为成功，并清零错误计数
                    if res.startswith("ok|正常状态") or res.startswith("ok|1"):
                        failure_count = 0
                        self.root.after(0, lambda: self.hb_lbl.config(foreground="green"))
                    else:
                        failure_count += 1
                        logger.debug(f"心跳异常 ({failure_count}/{max_failures}): {res}")
                        self.root.after(0, lambda: self.hb_lbl.config(foreground="orange"))

                        # 达到最大容错次数才触发退出
                        if failure_count >= max_failures:
                            logger.debug("心跳连续失败达到上限，准备退出")
                            self.root.after(0, self.on_closing)
                            break
                except Exception as e:
                    failure_count += 1
                    logger.debug(f"心跳请求异常 ({failure_count}/{max_failures}): {e}")
                    self.root.after(0, lambda: self.hb_lbl.config(foreground="orange"))

                    if failure_count >= max_failures:
                        logger.debug("网络请求连续异常达到上限，准备退出")
                        self.root.after(0, self.on_closing)
                        break

        self.hb_thread = threading.Thread(target=hb_task, daemon=True)
        self.hb_thread.start()

    def stop_heartbeat(self):
        self.hb_running = False
        try:
            self.hb_lbl.config(text="💤", foreground="gray")
        except Exception:
            pass

    # ---------------------------
    # 窗口关闭事件
    # ---------------------------
    def on_closing(self):
        # 1. 第一时间切断所有 loguru 日志输出，防止后台线程崩溃
        # logger.remove()

        self.save_card()
        self.stop_heartbeat()

        # 2. 瞬间隐藏窗口
        self.root.withdraw()

        # 3. 开启后台线程处理退出逻辑
        def exit_task():
            if getattr(self, 'api', None) and getattr(self, 'is_logged_in', False):
                try:
                    # 此时已经没有 logger 了，直接静默注销
                    self.api.user_logout()
                except Exception:
                    pass

            # 4. 强制杀掉当前程序的所有线程并退出
            import os
            os._exit(0)

        threading.Thread(target=exit_task, daemon=True).start()

    def on_connection_lost(self, msg):
        self.is_logged_in = False
        self.stop_heartbeat()
        self.status_lbl.config(text="● 断开", foreground="red")
        self.hb_lbl.config(text="💔", foreground="red")
        messagebox.showerror("连接断开", f"💔 与服务器连接断开\n\n{msg}\n\n请重新登录系统。")


def main():
    root = tk.Tk()

    # 优化在 Windows 上的清晰度
    # try:
    #     from ctypes import windll
    #     windll.shcore.SetProcessDpiAwareness(1)
    # except Exception:
    #     pass

    app = LoginApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == '__main__':
    main()
