import argparse
import json
from pathlib import Path
from urllib import request

try:
    import yaml
except ImportError:
    yaml = None


def load_yaml(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML not installed")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_url(url: str, headers: dict | None = None) -> tuple[bool, str]:
    try:
        req = request.Request(url, headers=headers or {})
        with request.urlopen(req, timeout=10) as res:
            return True, res.read().decode("utf-8")[:300]
    except Exception as exc:
        return False, str(exc)


def check_import(name: str) -> tuple[bool, str]:
    try:
        __import__(name)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deployment environment checker")
    parser.add_argument("--worker-config", default="automation_config.yaml")
    parser.add_argument("--discord-config", default="discord_config.yaml")
    parser.add_argument("--scheduler-config", default="scheduler_config.yaml")
    args = parser.parse_args()

    report = {}
    report["files"] = {
        args.worker_config: Path(args.worker_config).exists(),
        args.discord_config: Path(args.discord_config).exists(),
        args.scheduler_config: Path(args.scheduler_config).exists(),
    }
    report["imports"] = {
        "yaml": check_import("yaml"),
        "discord": check_import("discord"),
        "DrissionPage": check_import("DrissionPage"),
        "Crypto": check_import("Crypto"),
    }

    if Path(args.worker_config).exists():
        cfg = load_yaml(args.worker_config)
        report["worker_config"] = {
            "node_id": cfg.get("node_id"),
            "central_api": cfg.get("central_api"),
            "bit_api_url": cfg.get("bit_api_url"),
            "sync_group_ids": cfg.get("sync_group_ids"),
            "require_license": cfg.get("require_license"),
            "enable_grok_browser": cfg.get("enable_grok_browser"),
        }
        if cfg.get("central_api") and cfg.get("central_token"):
            ok, msg = check_url(
                f"{cfg['central_api'].rstrip('/')}/health",
                {"X-Automation-Token": cfg.get("central_token", "")},
            )
            report["central_health"] = {"ok": ok, "message": msg}
        if cfg.get("bit_api_url"):
            ok, msg = check_url(f"{cfg['bit_api_url'].rstrip('/')}/health")
            report["bit_api_health"] = {"ok": ok, "message": msg}

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

