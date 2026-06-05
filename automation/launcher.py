from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
import threading
import time
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("缺少 PyYAML，启动器无法读取配置") from exc

from automation.license_guard import LicenseGuard


CONFIG_PATH = Path("automation_config.yaml")


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        template = Path("automation_config.example.yaml")
        if template.exists():
            CONFIG_PATH.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            CONFIG_PATH.write_text(
                "node_id: PC-01\n"
                "label: Office PC 01\n"
                "central_api: https://your-domain.example\n"
                "central_token: change-me\n"
                "worker_enabled: true\n"
                "card_number: ''\n"
                "app_version: '1.0.0'\n"
                "require_license: true\n"
                "license_heartbeat_seconds: 60\n"
                "license_max_failures: 3\n"
                "bit_api_url: http://127.0.0.1:54345\n"
                "sync_group_ids: []\n",
                encoding="utf-8",
            )
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        broken = CONFIG_PATH.with_suffix(f".broken_{time.strftime('%Y%m%d_%H%M%S')}.yaml")
        try:
            CONFIG_PATH.replace(broken)
        except Exception:
            pass
        template = Path("automation_config.example.yaml")
        if template.exists():
            CONFIG_PATH.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        raise RuntimeError(f"automation_config.yaml 格式错误，已尝试备份到 {broken}: {exc}") from exc


def save_config(config: Dict[str, Any]) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def default_label_for_node(node_id: str) -> str:
    text = (node_id or "").strip() or "PC-01"
    suffix = text.rsplit("-", 1)[-1] if "-" in text else ""
    if text.upper().startswith("PC-") and suffix.isdigit():
        return f"Office PC {int(suffix):02d}"
    return text


def is_auto_label(label: str, old_node_id: str) -> bool:
    label = (label or "").strip()
    old_node_id = (old_node_id or "").strip()
    if not label:
        return True
    defaults = {
        old_node_id,
        default_label_for_node(old_node_id),
        "local-node",
        "PC-01",
        "Office PC 01",
        "Office PC 1",
    }
    return label in defaults


def exe_path(name: str, module: str) -> list[str]:
    base = Path(sys.executable).resolve().parent
    client_root = base.parent.parent if base.parent.name == "runtime" else base
    candidate = client_root / "runtime" / Path(name).stem / name
    if candidate.exists():
        return [str(candidate)]
    candidate = client_root / name
    if candidate.exists():
        return [str(candidate)]
    return [sys.executable, "-m", module]


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("XBot 客户端启动器")
        self.geometry("520x330")
        self.resizable(False, False)
        self.config_data = load_config()
        self.start_button: ttk.Button | None = None
        self.vars = {
            "card_number": tk.StringVar(value=str(self.config_data.get("card_number") or "")),
            "central_api": tk.StringVar(value=str(self.config_data.get("central_api") or "")),
            "central_token": tk.StringVar(value=str(self.config_data.get("central_token") or "")),
            "node_id": tk.StringVar(value=str(self.config_data.get("node_id") or "PC-01")),
            "sync_group_ids": tk.StringVar(value=",".join(self.config_data.get("sync_group_ids") or [])),
        }
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        fields = [
            ("卡密", "card_number", True),
            ("中央服务器", "central_api", False),
            ("中央Token", "central_token", True),
            ("电脑编号", "node_id", False),
            ("比特分组ID", "sync_group_ids", False),
        ]
        for row, (label, key, secret) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=6)
            entry = ttk.Entry(frame, textvariable=self.vars[key], width=46, show="*" if secret else "")
            entry.grid(row=row, column=1, sticky=tk.EW, pady=6)
        hint = ttk.Label(frame, text="多个比特分组ID用英文逗号分隔。首次启动会验证卡密，验证通过后保存配置。")
        hint.grid(row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=(8, 14))
        btns = ttk.Frame(frame)
        btns.grid(row=len(fields) + 1, column=0, columnspan=2, sticky=tk.E)
        self.start_button = ttk.Button(btns, text="保存并启动Worker", command=self.save_validate_start)
        self.start_button.pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="打开面板", command=self.open_panel).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="退出", command=self.destroy).pack(side=tk.LEFT, padx=4)
        frame.columnconfigure(1, weight=1)

    def collect_config(self) -> Dict[str, Any]:
        config = dict(self.config_data)
        old_node_id = str(self.config_data.get("node_id") or "PC-01")
        old_label = str(self.config_data.get("label") or "")
        config["card_number"] = self.vars["card_number"].get().strip()
        config["central_api"] = self.vars["central_api"].get().strip().rstrip("/")
        config["central_token"] = self.vars["central_token"].get().strip()
        config["node_id"] = self.vars["node_id"].get().strip() or "PC-01"
        if config["node_id"] != old_node_id and is_auto_label(old_label, old_node_id):
            config["label"] = default_label_for_node(config["node_id"])
        else:
            config["label"] = old_label or default_label_for_node(config["node_id"])
        groups = [item.strip() for item in self.vars["sync_group_ids"].get().split(",") if item.strip()]
        config["sync_group_ids"] = groups
        config["require_license"] = True
        config.setdefault("app_version", "1.0.0")
        config.setdefault("worker_enabled", True)
        config.setdefault("bit_api_url", "http://127.0.0.1:54345")
        config.setdefault("poll_interval_seconds", 5)
        config.setdefault("license_heartbeat_enabled", False)
        config.setdefault("license_heartbeat_seconds", 60)
        config.setdefault("license_max_failures", 3)
        config.setdefault("worker_autorestart", True)
        config.setdefault("worker_restart_interval_seconds", 10)
        return config

    def save_validate_start(self) -> None:
        config = self.collect_config()
        missing = [key for key in ("card_number", "central_api", "central_token", "node_id") if not config.get(key)]
        if missing:
            messagebox.showwarning("缺少配置", "请填写：" + "、".join(missing))
            return
        if self.start_button:
            self.start_button.configure(state=tk.DISABLED, text="验证中...")
        threading.Thread(target=self._validate_and_start_worker, args=(config,), daemon=True).start()

    def _validate_and_start_worker(self, config: Dict[str, Any]) -> None:
        guard = LicenseGuard(config.get("card_number"), config.get("app_version", "1.0.0"))
        result = guard.validate_once()
        self.after(0, lambda: self._finish_validate_and_start(config, result.ok, result.message))

    def _finish_validate_and_start(self, config: Dict[str, Any], ok: bool, message: str) -> None:
        if self.start_button:
            self.start_button.configure(state=tk.NORMAL, text="保存并启动Worker")
        if not ok:
            messagebox.showerror("卡密验证失败", message)
            return
        save_config(config)
        self.start_worker()
        messagebox.showinfo("验证成功", "卡密验证成功，Worker 已在后台启动。")

    def start_worker(self) -> None:
        cmd = exe_path("XBotSupervisor.exe", "automation.supervisor")
        subprocess.Popen([*cmd, "--config", str(CONFIG_PATH)], cwd=os.getcwd(), creationflags=0)

    def open_panel(self) -> None:
        cmd = exe_path("XBotPanel.exe", "automation.panel_entry")
        subprocess.Popen(cmd, cwd=os.getcwd(), creationflags=subprocess.CREATE_NEW_CONSOLE)


def main() -> None:
    app = Launcher()
    app.mainloop()


if __name__ == "__main__":
    main()
