from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yaml
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "缺少 PyYAML，无法读取 config.yaml。请运行："
        "powershell -ExecutionPolicy Bypass -File deployment/install_worker_deps.ps1"
    ) from exc

from automation.license_guard import LicenseGuard

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def validate_tool_license() -> None:
    automation_config = load_yaml(Path("automation_config.yaml"))
    if not automation_config:
        if getattr(sys, "frozen", False):
            raise RuntimeError("未找到 automation_config.yaml，无法验证卡密")
        return
    if not automation_config.get("require_license", False):
        return
    card_number = str(automation_config.get("card_number") or "").strip()
    if not card_number:
        raise RuntimeError("未配置卡密，请先运行 launch_xbot.bat 完成卡密验证")
    result = LicenseGuard(card_number, str(automation_config.get("app_version") or "1.0.0")).validate_once()
    if not result.ok:
        raise RuntimeError(f"卡密验证失败：{result.message}")


def build_runtime_config(base_config: Dict[str, Any], mode: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    config = dict(base_config)
    config["RUN_MODE"] = mode
    if payload.get("group_id"):
        config["GROUP_ID"] = payload["group_id"]
    if payload.get("profile_id"):
        config["PROFILE_ID"] = payload["profile_id"]
        config["PROFILE_NAME"] = payload.get("profile_name") or payload.get("account_id") or payload["profile_id"]
    if payload.get("window_count"):
        config["WINDOW_COUNT"] = int(payload["window_count"])
    if payload.get("_job_id"):
        config["CURRENT_JOB_ID"] = str(payload["_job_id"])
    for section, values in (payload.get("config_overrides") or {}).items():
        if isinstance(values, dict):
            merged = dict(config.get(section) or {})
            merged.update(values)
            config[section] = merged

    if mode == 2 and payload.get("target_urls"):
        target = dict(config.get("TARGET_BOOST_CONFIG") or {})
        target["target_urls"] = payload["target_urls"]
        config["TARGET_BOOST_CONFIG"] = target

    if mode == 3:
        post = dict(config.get("POST_CONFIG") or {})
        if payload.get("txt_path"):
            post["txt_path"] = payload["txt_path"]
        if payload.get("img_folder"):
            post["img_folder"] = payload["img_folder"]
        config["POST_CONFIG"] = post

    return config


def validate_runtime_config(config: Dict[str, Any], mode: int) -> None:
    missing = []
    if not config.get("GROUP_ID") and not config.get("PROFILE_ID"):
        missing.append("基础设置 / 浏览器分组 ID")
    if int(config.get("WINDOW_COUNT") or 0) <= 0:
        missing.append("基础设置 / 启动窗口数量")

    if mode == 1:
        farm = config.get("FARMING_CONFIG") or {}
        if not farm:
            missing.append("养号全量配置")
        if not farm.get("keywords"):
            missing.append("养号全量配置 / 搜索关键词")
        for key, label in {
            "switch_interval_min": "模块停留最小秒数",
            "switch_interval_max": "模块停留最大秒数",
            "read_delay_min": "阅读延迟最小秒数",
            "read_delay_max": "阅读延迟最大秒数",
        }.items():
            if key not in farm:
                missing.append(f"养号全量配置 / {label}")

    if mode == 2:
        boost = config.get("TARGET_BOOST_CONFIG") or {}
        if not boost.get("target_urls"):
            missing.append("冲贴配置 / 目标冲贴 URLs")

    if mode == 3:
        post = config.get("POST_CONFIG") or {}
        txt_path = str(post.get("txt_path") or "").strip()
        img_folder = str(post.get("img_folder") or "").strip()
        if not txt_path:
            missing.append("模式三文本库未配置")
        elif not Path(txt_path).is_file():
            missing.append(f"模式三文本库不存在：{txt_path}")
        if not img_folder:
            missing.append("模式三媒体照片库未配置")
        elif not Path(img_folder).is_dir():
            missing.append(f"模式三媒体照片库不存在：{img_folder}")

    if missing:
        raise RuntimeError(
            "自动执行参数不完整，请先打开参数面板填写并点击“手动保存配置”。缺少："
            + "；".join(dict.fromkeys(missing))
        )


def import_legacy_module() -> Any:
    try:
        import newtkmain
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        raise RuntimeError(
            f"原脚本依赖或文件导入失败：{missing}。"
            "请确认在 worker 使用的 Python 环境中已运行 deployment/install_worker_deps.ps1。"
        ) from exc
    return newtkmain


class LegacyRunTracker:
    def __init__(self, mode: int):
        self.mode = mode
        self.lines: List[str] = []
        self.started_profiles = 0
        self.entered_mode = False
        self.entered_business_loop = False
        self.completed = False
        self.stopped = False
        self.errors: List[str] = []
        self.started_at = time.time()
        self.last_progress_emit = 0.0

    def write(self, message: Any) -> None:
        text = str(message).strip()
        if not text:
            return
        clean = self.clean_log(text)
        if not clean:
            return
        self.lines.append(clean)
        self.lines = self.lines[-80:]
        if self.should_emit(clean):
            print(json.dumps({"legacy_log": clean}, ensure_ascii=True), flush=True)
        if "拉起成功" in clean:
            self.started_profiles += 1
        if f"开始模式 {self.mode}" in clean or f"开始模式{self.mode}" in clean:
            self.entered_mode = True
        if (
            ">>> 循环" in clean
            or "启动【集体多链接冲贴模式】" in clean
            or "启动【自动发帖模式】" in clean
            or "冲贴线程已启动" in clean
            or "正在执行目标" in clean
            or "发帖任务" in clean
        ):
            self.entered_business_loop = True
        if (
            "任务全部正常结束" in clean
            or "本窗口任务目标已全部达成" in clean
            or "所有账号的发帖任务已圆满结束" in clean
            or "分配的所有冲贴任务已完成" in clean
        ):
            self.completed = True
        if "任务已被手动中止" in clean:
            self.stopped = True
        if any(flag in clean for flag in ("ERROR", "错误", "异常", "未登录", "未获取到任何窗口", "未成功拉起任何浏览器窗口")):
            self.errors.append(clean)
            self.errors = self.errors[-20:]

    def flush(self) -> None:
        return

    def result(self) -> Dict[str, Any]:
        status = "completed"
        ok = True
        reason = ""
        if self.stopped:
            ok = False
            status = "stopped"
            reason = "任务被停止"
        elif self.errors and not self.completed:
            ok = False
            status = "failed"
            reason = self.errors[-1]
        elif not self.entered_mode:
            ok = False
            status = "not_started"
            reason = "原脚本没有进入对应模式"
        elif self.mode == 1 and not self.entered_business_loop:
            ok = False
            status = "not_really_running"
            reason = "模式一没有进入养号循环，通常是账号未登录、页面异常或窗口未正常打开"
        return {
            "ok": ok,
            "status": status,
            "reason": reason,
            "mode": self.mode,
            "started_profiles": self.started_profiles,
            "entered_mode": self.entered_mode,
            "entered_business_loop": self.entered_business_loop,
            "completed": self.completed,
            "duration_seconds": int(time.time() - self.started_at),
            "summary_logs": self.summary_lines(),
            "errors": self.errors,
        }

    def summary_lines(self) -> List[str]:
        important = []
        for line in self.lines:
            if self.is_important(line):
                important.append(line)
        return important[-20:]

    def should_emit(self, line: str) -> bool:
        if not self.is_important(line):
            return False
        if ">>> 循环" in line or "进度 ->" in line:
            now = time.time()
            if now - self.last_progress_emit < 60:
                return False
            self.last_progress_emit = now
        return True

    @staticmethod
    def is_important(line: str) -> bool:
        patterns = (
            "引擎已启动",
            "拉起成功",
            "窗口横向重叠排列完毕",
            "开始模式",
            ">>> 循环",
            "进度 ->",
            "执行了：",
            "冲贴线程已启动",
            "正在执行目标",
            "转帖成功",
            "评论成功",
            "分配的所有冲贴任务已完成",
            "潮汐休眠",
            "休息结束",
            "任务目标已全部达成",
            "任务全部正常结束",
            "未登录",
            "未获取到任何窗口",
            "未成功拉起任何浏览器窗口",
            "异常",
            "错误",
        )
        return any(pattern in line for pattern in patterns)

    @staticmethod
    def clean_log(text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def run_legacy_mode(config: Dict[str, Any], log_file: str = "") -> Dict[str, Any]:
    newtkmain = import_legacy_module()
    mode = int(config.get("RUN_MODE", 1))
    tracker = LegacyRunTracker(mode)

    try:
        newtkmain.logger.remove()
    except Exception:
        pass
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        newtkmain.logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            encoding="utf-8",
        )
    newtkmain.logger.add(tracker, format="{time:HH:mm:ss} | {level} | {message}")

    newtkmain.RUN_MODE = mode
    newtkmain.BIT_API_URL = config.get("BIT_API_URL", "http://127.0.0.1:54345")
    newtkmain.GROUP_ID = config.get("GROUP_ID", "")
    newtkmain.PROFILE_ID = config.get("PROFILE_ID", "")
    newtkmain.PROFILE_NAME = config.get("PROFILE_NAME", "")
    newtkmain.WINDOW_COUNT = int(config.get("WINDOW_COUNT", 3))
    newtkmain.COMMENT_TEXTS = config.get("COMMENT_TEXTS", [])
    newtkmain.FARMING_CONFIG = config.get("FARMING_CONFIG", {})
    newtkmain.TARGET_BOOST_CONFIG = config.get("TARGET_BOOST_CONFIG", {})
    newtkmain.POST_CONFIG = config.get("POST_CONFIG", {})
    newtkmain.CURRENT_JOB_ID = str(config.get("CURRENT_JOB_ID") or "")
    newtkmain.app_instance = None
    newtkmain.bot_worker()
    result = tracker.result()
    print(json.dumps({"legacy_result": result}, ensure_ascii=True))
    if not result["ok"]:
        raise RuntimeError(result["reason"] or f"原脚本模式{mode}没有正常完成")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run existing X script modes without GUI")
    parser.add_argument("--mode", type=int, required=True, choices=[1, 2, 3])
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--check-import", action="store_true")
    parser.add_argument("--check-smart-comment", action="store_true")
    args = parser.parse_args()

    if args.check_import:
        import_legacy_module()
        print("legacy import ok")
        return
    if args.check_smart_comment:
        from automation.model_client import OpenAICompatibleClient, app_root, load_model_config
        from automation.smart_comment import smart_comment_config

        model_config = load_model_config()
        client = OpenAICompatibleClient(model_config)
        data = {
            "app_root": str(app_root()),
            "model_enabled": bool(model_config.get("enabled")),
            "model_ready": client.ready(),
            "base_url": client.base_url,
            "model": client.model,
            "smart_comment": smart_comment_config(),
        }
        print(json.dumps(data, ensure_ascii=True), flush=True)
        return

    validate_tool_license()
    payload = json.loads(args.payload_json or "{}")
    config_path = Path(args.config)
    base_config = load_yaml(config_path)
    if not base_config:
        raise RuntimeError(
            "没有找到 config.yaml。请先打开参数面板填写参数，并点击“手动保存配置”。"
        )
    runtime_config = build_runtime_config(base_config, args.mode, payload)
    validate_runtime_config(runtime_config, args.mode)
    save_yaml(Path("automation/data/last_legacy_runtime_config.yaml"), runtime_config)
    run_legacy_mode(runtime_config, args.log_file)


if __name__ == "__main__":
    main()

