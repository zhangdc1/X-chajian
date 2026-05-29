import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def load_config(path: str) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required for scheduler config")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def post_json(url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Automation-Token": token,
        },
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def run_forever(config: Dict[str, Any]) -> None:
    api = config["central_api"].rstrip("/")
    token = config["central_token"]
    interval = int(config.get("scheduler_interval_seconds", 30))
    limit = int(config.get("dispatch_limit", 100))
    print(f"scheduler dispatch loop started: interval={interval}s")
    while True:
        try:
            result = post_json(f"{api}/scheduler/dispatch", token, {"limit": limit})
            count = result.get("count", 0)
            if count:
                print(f"dispatched due tasks: {count}")
        except Exception as exc:
            print(f"scheduler dispatch failed: {exc}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Central scheduled-task dispatcher")
    parser.add_argument("--config", default="scheduler_config.yaml")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.once:
        result = post_json(
            f"{config['central_api'].rstrip('/')}/scheduler/dispatch",
            config["central_token"],
            {"limit": int(config.get("dispatch_limit", 100))},
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    run_forever(config)


if __name__ == "__main__":
    main()
