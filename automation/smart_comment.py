from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from automation.model_client import OpenAICompatibleClient, load_model_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "automation" / "output" / "comment_drafts.jsonl"


def smart_comment_config() -> Dict[str, Any]:
    config = load_model_config()
    smart = config.get("smart_comment") or {}
    return {
        "enabled": bool(smart.get("enabled", False)),
        "auto_publish": bool(smart.get("auto_publish", True)),
        "save_drafts": bool(smart.get("save_drafts", True)),
        "fallback_to_comment_library": bool(smart.get("fallback_to_comment_library", True)),
        "output_path": smart.get("output_path") or str(DEFAULT_OUTPUT),
    }


def generate_comment(
    tweet_text: str,
    tweet_url: str = "",
    author: str = "",
    profile: str = "",
    fallback_pool: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    cfg = smart_comment_config()
    fallback_text = next((x for x in (fallback_pool or []) if str(x).strip()), "")
    record: Dict[str, Any] = {
        "time": int(time.time()),
        "profile": profile,
        "tweet_url": tweet_url,
        "tweet_text": (tweet_text or "")[:2000],
        "author": author,
        "generated_comment": "",
        "model": "",
        "status": "generated",
        "publish_result": "",
        "error": "",
    }
    if not cfg["enabled"]:
        record["generated_comment"] = fallback_text
        record["status"] = "fallback_used"
        record["error"] = "smart_comment disabled"
        return record

    client = OpenAICompatibleClient.from_file()
    try:
        comment = client.chat_text(
            [
                {
                    "role": "system",
                    "content": (
                        "你为 X 帖子生成中文短评论。要求：自然、具体、贴合主题；"
                        "不要像广告，不要带网址，不要使用夸张营销语；长度 8-35 个中文字符。只输出评论本身。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"作者：{author}\n链接：{tweet_url}\n帖子内容：{tweet_text}\n请给一条适合直接发布的评论。",
                },
            ],
            temperature=0.6,
        )
        comment = sanitize_comment(comment)
        if not comment:
            raise RuntimeError("模型返回空评论")
        record["generated_comment"] = comment
        record["model"] = client.model
        record["status"] = "generated"
        return record
    except Exception as exc:
        record["error"] = str(exc)
        if cfg["fallback_to_comment_library"] and fallback_text:
            record["generated_comment"] = fallback_text
            record["status"] = "fallback_used"
            return record
        raise


def sanitize_comment(text: str) -> str:
    text = (text or "").strip().strip('"').strip("'")
    text = text.replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:120]


def save_comment_record(record: Dict[str, Any], status: Optional[str] = None, publish_result: str = "", error: str = "") -> None:
    cfg = smart_comment_config()
    if not cfg["save_drafts"]:
        return
    item = dict(record)
    if status:
        item["status"] = status
    if publish_result:
        item["publish_result"] = publish_result
    if error:
        item["error"] = error
    path = Path(cfg["output_path"])
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
