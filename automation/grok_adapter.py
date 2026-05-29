import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass
class GrokResult:
    ok: bool
    raw_response: str
    error: str = ""


class GrokBrowserAdapter:
    """Browser adapter for X built-in Grok.

    The selectors are configurable because X changes its DOM frequently. This
    adapter is deliberately narrow: it submits a prompt and extracts text. The
    worker decides whether to call it.
    """

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def ask_with_debug_port(self, debug_port: int, prompt: str) -> GrokResult:
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except Exception as exc:
            return GrokResult(False, "", f"DrissionPage import failed: {exc}")

        try:
            co = ChromiumOptions()
            co.set_local_port(int(debug_port))
            page = ChromiumPage(co)
            self._close_extra_tabs(page)

            grok_url = self.settings.get("grok_url", "https://x.com/i/grok")
            page.get(grok_url)
            page.wait.load_start()
            self._sleep(float(self.settings.get("initial_wait_seconds", 5)))

            input_ele = self._first_ele(page, self.settings.get("input_selectors", []), timeout=8)
            if not input_ele:
                return GrokResult(False, "", "Grok input element not found")
            input_ele.click()
            self._sleep(0.5)
            page.actions.type(prompt)
            self._sleep(0.5)

            send_ele = self._first_ele(page, self.settings.get("send_selectors", []), timeout=3)
            if send_ele:
                send_ele.click(by_js=True)
            else:
                page.actions.type("\n")

            raw = self._wait_for_response(page)
            if not raw:
                return GrokResult(False, "", "Grok response text not found")
            return GrokResult(True, raw)
        except Exception as exc:
            return GrokResult(False, "", str(exc))

    def _wait_for_response(self, page: Any) -> str:
        timeout = float(self.settings.get("response_timeout_seconds", 120))
        poll = float(self.settings.get("response_poll_seconds", 3))
        end = time.time() + timeout
        last_text = ""
        stable_count = 0
        while time.time() < end:
            text = self._extract_response_text(page)
            if text and text == last_text:
                stable_count += 1
            elif text:
                stable_count = 0
                last_text = text
            if last_text and stable_count >= 2:
                return last_text
            self._sleep(poll)
        return last_text

    def _extract_response_text(self, page: Any) -> str:
        selectors = self.settings.get("response_selectors", [])
        texts = []
        for selector in selectors:
            try:
                elements = page.eles(selector, timeout=1)
            except Exception:
                elements = []
            for ele in elements or []:
                try:
                    text = (ele.text or "").strip()
                except Exception:
                    text = ""
                if text:
                    texts.append(text)
        if texts:
            return "\n\n".join(texts[-3:])
        try:
            body = page.ele("tag:body", timeout=1)
            return (body.text or "").strip() if body else ""
        except Exception:
            return ""

    @staticmethod
    def _first_ele(page: Any, selectors: Iterable[str], timeout: float = 5) -> Optional[Any]:
        for selector in selectors:
            try:
                ele = page.ele(selector, timeout=timeout)
                if ele:
                    return ele
            except Exception:
                continue
        return None

    @staticmethod
    def _close_extra_tabs(page: Any) -> None:
        try:
            tabs_to_close = [t for t in page.tab_ids if t != page.tab_id]
            if tabs_to_close:
                page.close_tabs(tabs_to_close)
        except Exception:
            pass

    @staticmethod
    def _sleep(seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

