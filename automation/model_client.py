from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        if exe_dir.parent.name.lower() == "runtime":
            return exe_dir.parent.parent
        if exe_dir.name.lower() == "runtime":
            return exe_dir.parent
        return exe_dir
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = app_root()


def load_model_config(path: str | Path = "model_config.yaml") -> Dict[str, Any]:
    if yaml is None:
        return {"enabled": False}
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        return {"enabled": False}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"enabled": False}


class OpenAICompatibleClient:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = bool(config.get("enabled"))
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        self.api_key = str(config.get("api_key") or "")
        self.model = str(config.get("model") or "")
        self.timeout = int(config.get("timeout_seconds") or 30)
        self.temperature = float(config.get("temperature", 0.1))

    @classmethod
    def from_file(cls, path: str | Path = "model_config.yaml") -> "OpenAICompatibleClient":
        return cls(load_model_config(path))

    def ready(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key and self.model)

    def chat_text(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> str:
        if not self.ready():
            raise RuntimeError("模型未启用或配置不完整，请检查 model_config.yaml")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        opener = request.build_opener(request.ProxyHandler({}))
        with opener.open(req, timeout=self.timeout) as res:
            body = json.loads(res.read().decode("utf-8"))
        return str(body["choices"][0]["message"]["content"]).strip()

    def chat_json(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> Dict[str, Any]:
        text = self.chat_text(messages, temperature)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end >= start:
            text = text[start : end + 1]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("模型返回的 JSON 不是对象")
        return data
