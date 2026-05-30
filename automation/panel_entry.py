from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import yaml

from automation.license_guard import LicenseGuard
from newtkmain import AppGUI


CONFIG_PATH = Path("automation_config.yaml")


def validate_license_or_exit() -> bool:
    if not CONFIG_PATH.exists():
        messagebox.showerror("卡密验证失败", "未找到 automation_config.yaml，请先运行 launch_xbot.bat 完成配置。")
        return False
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        messagebox.showerror("卡密验证失败", f"读取 automation_config.yaml 失败：{exc}")
        return False

    if not config.get("require_license", True):
        return True
    card_number = str(config.get("card_number") or "").strip()
    if not card_number:
        messagebox.showerror("卡密验证失败", "未配置卡密，请先运行 launch_xbot.bat 输入并验证卡密。")
        return False
    result = LicenseGuard(card_number, str(config.get("app_version") or "1.0.0")).validate_once()
    if not result.ok:
        messagebox.showerror("卡密验证失败", result.message)
        return False
    return True


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    if not validate_license_or_exit():
        root.destroy()
        return
    AppGUI(root, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
