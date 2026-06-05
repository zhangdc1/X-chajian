import subprocess
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
            ok, entered_len = self._fill_prompt(page, input_ele, prompt)
            if not ok:
                return GrokResult(
                    False,
                    "",
                    f"提示词未完整输入：输入长度 {entered_len}/{len(prompt)}，已停止发送，避免生成错误计划",
                )

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

    def _fill_prompt(self, page: Any, input_ele: Any, prompt: str) -> tuple[bool, int]:
        prompt = prompt or ""
        if not prompt:
            return True, 0
        for attempt in range(2):
            try:
                input_ele.click()
            except Exception:
                pass
            self._sleep(0.2)
            if self._try_js_set(page, input_ele, prompt):
                self._sleep(0.4)
                length = self._input_text_length(page, input_ele)
                if self._enough_text(length, prompt):
                    return True, length
            if self._try_clipboard_paste(page, prompt):
                self._sleep(0.5)
                length = self._input_text_length(page, input_ele)
                if self._enough_text(length, prompt):
                    return True, length
            try:
                input_ele.click()
                page.actions.type(prompt)
            except Exception:
                pass
            self._sleep(0.5)
            length = self._input_text_length(page, input_ele)
            if self._enough_text(length, prompt):
                return True, length
            if attempt == 0:
                self._clear_input(page, input_ele)
        return False, self._input_text_length(page, input_ele)

    @staticmethod
    def _enough_text(length: int, prompt: str) -> bool:
        expected = len(prompt or "")
        return expected <= 20 or length >= int(expected * 0.9)

    def _try_js_set(self, page: Any, input_ele: Any, prompt: str) -> bool:
        escaped = self._js_string(prompt)
        scripts = [
            """
            const el = document.activeElement;
            const value = __PROMPT__;
            if (!el) return false;
            if ('value' in el) el.value = value;
            else el.textContent = value;
            el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:value}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
            """,
            """
            const value = __PROMPT__;
            const selectors = ['textarea', '[contenteditable="true"]', '[role="textbox"]'];
            const el = selectors.map(s => document.querySelector(s)).find(Boolean);
            if (!el) return false;
            el.focus();
            if ('value' in el) el.value = value;
            else el.textContent = value;
            el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:value}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
            return true;
            """,
        ]
        for script in scripts:
            try:
                if page.run_js(script.replace("__PROMPT__", escaped)):
                    return True
            except Exception:
                continue
        try:
            input_ele.input(prompt, clear=True)
            return True
        except Exception:
            return False

    def _try_clipboard_paste(self, page: Any, prompt: str) -> bool:
        try:
            self._set_clipboard(prompt)
        except Exception:
            return False
        try:
            page.actions.key_down("CTRL").type("v").key_up("CTRL")
            return True
        except Exception:
            try:
                page.actions.type("\ue009v")
                return True
            except Exception:
                return False

    @staticmethod
    def _set_clipboard(text: str) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
            return
        except Exception:
            pass
        subprocess.run(
            ["clip"],
            input=text,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _input_text_length(self, page: Any, input_ele: Any) -> int:
        values = []
        for getter in (
            lambda: input_ele.attr("value"),
            lambda: input_ele.text,
            lambda: page.run_js(
                """
                const el = document.activeElement || document.querySelector('textarea,[contenteditable="true"],[role="textbox"]');
                if (!el) return '';
                return ('value' in el ? el.value : el.innerText || el.textContent || '');
                """
            ),
        ):
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                values.append(str(value))
        return max((len(value.strip()) for value in values), default=0)

    def _clear_input(self, page: Any, input_ele: Any) -> None:
        try:
            input_ele.click()
            page.actions.key_down("CTRL").type("a").key_up("CTRL").type("\b")
            return
        except Exception:
            pass
        try:
            page.run_js(
                """
                const el = document.activeElement || document.querySelector('textarea,[contenteditable="true"],[role="textbox"]');
                if (!el) return;
                if ('value' in el) el.value = '';
                else el.textContent = '';
                el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'deleteContentBackward'}));
                """
            )
        except Exception:
            pass

    @staticmethod
    def _js_string(text: str) -> str:
        import json

        return json.dumps(text, ensure_ascii=False)

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
