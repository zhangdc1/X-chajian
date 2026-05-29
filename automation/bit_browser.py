import json
from typing import Any, Dict, List
from urllib import request


class BitBrowserClient:
    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")

    def list_profiles(self, group_id: str, page_size: int = 100) -> List[Dict[str, Any]]:
        page = 0
        profiles: List[Dict[str, Any]] = []
        while True:
            payload = {"groupId": group_id, "page": page, "pageSize": page_size}
            response = self._post("/browser/list", payload)
            if not response.get("success"):
                raise RuntimeError(f"Bit Browser list failed: {response}")
            items = response.get("data", {}).get("list", []) or []
            for item in items:
                profiles.append(
                    {
                        "profile_id": item.get("id"),
                        "profile_name": item.get("name") or "",
                        "group_id": group_id,
                        "meta": item,
                    }
                )
            if len(items) < page_size:
                break
            page += 1
        return profiles

    def open_profile(self, profile_id: str) -> Dict[str, Any]:
        response = self._post("/browser/open", {"id": profile_id})
        if not response.get("success"):
            raise RuntimeError(f"Bit Browser open failed: {response}")
        return response.get("data", {}) or {}

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.api_url}{path}",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=20) as res:
            return json.loads(res.read().decode("utf-8"))
