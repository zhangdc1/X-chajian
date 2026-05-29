from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Tail a local automation log in the original GUI log area")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()

    import newtkmain

    log_path = Path(args.log_file)
    root = tk.Tk()
    root.withdraw()
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
