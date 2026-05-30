from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from automation.license_guard import LicenseGuard


def validate_license_or_exit() -> bool:
    config_path = Path("automation_config.yaml")
    if not config_path.exists():
        messagebox.showerror("卡密验证失败", "未找到 automation_config.yaml，请先运行 launch_xbot.bat 完成配置。")
        return False
    if yaml is None:
        messagebox.showerror("卡密验证失败", "缺少 PyYAML，无法读取 automation_config.yaml。")
        return False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
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
    parser = argparse.ArgumentParser(description="Tail a local automation log in the original GUI log area")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()

    import newtkmain

    log_path = Path(args.log_file)
    root = tk.Tk()
    root.withdraw()
    if not validate_license_or_exit():
        root.destroy()
        return
    app = newtkmain.AppGUI(root, root.destroy)
    app.title(f"自动化任务日志 - job {args.job_id or ''}")
    try:
        app.btn_start.config(state=tk.DISABLED, text="自动化任务执行中")
        app.btn_stop.config(state=tk.DISABLED)
    except Exception:
        pass

    state = {"pos": 0}

    def append(text: str) -> None:
        app.log_text.insert(tk.END, text)
        app.log_text.see(tk.END)

    append(f"\n===== 正在查看自动化日志：{log_path} =====\n")

    def poll() -> None:
        try:
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(state["pos"])
                    chunk = f.read()
                    state["pos"] = f.tell()
                if chunk:
                    append(chunk)
        except Exception as exc:
            append(f"\n[日志查看错误] {exc}\n")
        app.after(1000, poll)

    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
