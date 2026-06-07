from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


CONFIG_PATH = Path("automation_config.yaml")
STOP_EVENT = threading.Event()


def load_config(path: str | Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return str(pid) in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def claim_single_instance(config_path: str | Path) -> bool:
    lock_dir = Path("automation") / "local_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_name = str(Path(config_path)).replace("\\", "_").replace("/", "_").replace(":", "_")
    lock_path = lock_dir / f"supervisor_{safe_name}.pid"
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old_pid = 0
        if pid_is_running(old_pid):
            return False
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def exe_path(name: str, module: str) -> list[str]:
    base = Path(sys.executable).resolve().parent
    client_root = base.parent.parent if base.parent.name.lower() == "runtime" else base
    candidate = client_root / "runtime" / Path(name).stem / name
    if candidate.exists():
        return [str(candidate)]
    candidate = client_root / name
    if candidate.exists():
        return [str(candidate)]
    return [sys.executable, "-m", module]


class WorkerSupervisor:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.config = load_config(self.config_path)
        self.restart_interval = int(self.config.get("worker_restart_interval_seconds", 10) or 10)
        self.autorestart = bool(self.config.get("worker_autorestart", True))
        self.worker: Optional[subprocess.Popen] = None
        self.log_dir = Path("logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "worker_runtime.log"
        self.log_file = None

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
        with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(line)

    def worker_command(self) -> list[str]:
        return exe_path("XBotWorker.exe", "automation.worker") + ["--config", str(self.config_path)]

    def start_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            return
        self.log_file = open(self.log_path, "a", encoding="utf-8", errors="replace")
        flags = 0
        startupinfo = None
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.worker = subprocess.Popen(
            self.worker_command(),
            cwd=os.getcwd(),
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            startupinfo=startupinfo,
        )
        self.log(f"worker started pid={self.worker.pid}")

    def stop_worker(self) -> None:
        if not self.worker or self.worker.poll() is not None:
            return
        self.log(f"stopping worker pid={self.worker.pid}")
        self.worker.terminate()
        try:
            self.worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.worker.kill()
            self.worker.wait(timeout=10)

    def restart_worker(self) -> None:
        self.stop_worker()
        self.start_worker()

    def loop(self) -> None:
        self.start_worker()
        while not STOP_EVENT.is_set():
            if self.worker and self.worker.poll() is not None:
                code = self.worker.returncode
                self.log(f"worker exited code={code}")
                if not self.autorestart:
                    break
                time.sleep(max(1, self.restart_interval))
                self.start_worker()
            time.sleep(2)
        self.stop_worker()
        self.log("supervisor exited")


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path.resolve()))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path.resolve())])


def run_tray(supervisor: WorkerSupervisor) -> bool:
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as exc:
        supervisor.log(f"tray unavailable, running headless: {exc}")
        return False

    image = Image.new("RGB", (64, 64), "#1f6feb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((14, 14, 50, 50), outline="white", width=4)
    draw.text((22, 22), "X", fill="white")

    def open_panel(icon, item) -> None:
        subprocess.Popen(exe_path("XBotPanel.exe", "automation.panel_entry"), cwd=os.getcwd())

    def restart(icon, item) -> None:
        supervisor.restart_worker()

    def open_logs(icon, item) -> None:
        open_path(supervisor.log_dir)

    def quit_app(icon, item) -> None:
        STOP_EVENT.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("打开面板", open_panel),
        pystray.MenuItem("重启 Worker", restart),
        pystray.MenuItem("打开日志目录", open_logs),
        pystray.MenuItem("退出客户端", quit_app),
    )
    icon = pystray.Icon("XBot", image, "XBot 客户端", menu)
    threading.Thread(target=supervisor.loop, daemon=True).start()
    icon.run()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep XBotWorker running in the background")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    args = parser.parse_args()
    if not claim_single_instance(args.config):
        return
    supervisor = WorkerSupervisor(args.config)
    if not run_tray(supervisor):
        supervisor.loop()


if __name__ == "__main__":
    main()
