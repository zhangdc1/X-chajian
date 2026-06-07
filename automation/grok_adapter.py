import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional


@dataclass
class GrokResult:
    ok: bool
    raw_response: str
    error: str = ""


class GrokBrowserAdapter:
    """Browser adapter for X built-in Grok."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def ask_with_debug_port(
        self,
        debug_port: int,
        prompt: str,
        response_validator: Optional[Callable[[str], bool]] = None,
    ) -> GrokResult:
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

            if not self._send_prompt(page, input_ele):
                return GrokResult(False, "", "Grok 提示词已输入但发送按钮未激活，已停止，避免生成错误计划")

            raw = self._wait_for_response(page, response_validator=response_validator)
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

            # Prefer real user-like input. Some Grok pages show JS-set text but
            # keep the send button disabled because React did not receive input.
            for writer in (
                lambda: self._try_clipboard_paste(page, prompt),
                lambda: self._try_direct_input(page, input_ele, prompt),
                lambda: self._try_js_set(page, input_ele, prompt),
            ):
                if not writer():
                    continue
                self._sleep(0.6)
                length = self._input_text_length(page, input_ele)
                if self._enough_text(length, prompt):
                    self._nudge_input(page, input_ele)
                    return True, length

            if attempt == 0:
                self._clear_input(page, input_ele)
        return False, self._input_text_length(page, input_ele)

    @staticmethod
    def _enough_text(length: int, prompt: str) -> bool:
        expected = len(prompt or "")
        return expected <= 20 or length >= int(expected * 0.9)

    def _try_direct_input(self, page: Any, input_ele: Any, prompt: str) -> bool:
        try:
            input_ele.click()
            input_ele.input(prompt, clear=True)
            return True
        except Exception:
            pass
        try:
            input_ele.click()
            page.actions.type(prompt)
            return True
        except Exception:
            return False

    def _try_js_set(self, page: Any, input_ele: Any, prompt: str) -> bool:
        escaped = self._js_string(prompt)
        scripts = [
            """
            const el = document.activeElement;
            const value = __PROMPT__;
            if (!el) return false;
            el.focus();
            if ('value' in el) el.value = value;
            else {
              el.textContent = value;
              el.innerText = value;
            }
            el.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, inputType:'insertText', data:value}));
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
            else {
              el.textContent = value;
              el.innerText = value;
            }
            el.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, inputType:'insertText', data:value}));
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

    def _nudge_input(self, page: Any, input_ele: Any) -> None:
        try:
            input_ele.click()
            page.actions.type(" ").type("\b")
        except Exception:
            pass
        try:
            page.run_js(
                """
                const el = document.activeElement || document.querySelector('textarea,[contenteditable="true"],[role="textbox"]');
                if (!el) return;
                el.dispatchEvent(new KeyboardEvent('keydown', {key:' ', bubbles:true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {key:' ', bubbles:true}));
                el.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:' '}));
                """
            )
        except Exception:
            pass

    def _send_prompt(self, page: Any, input_ele: Any) -> bool:
        for _ in range(2):
            self._sleep(float(self.settings.get("send_ready_wait_seconds", 1)))
            send_ele = self._find_send_button(page)
            if send_ele:
                for by_js in (False, True):
                    try:
                        send_ele.click(by_js=by_js)
                        self._sleep(1.2)
                        if self._looks_submitted(page):
                            return True
                    except Exception:
                        continue
            if self._click_send_button_by_js(page):
                self._sleep(1.2)
                if self._looks_submitted(page):
                    return True
            for action in ("enter", "ctrl_enter"):
                try:
                    input_ele.click()
                    if action == "enter":
                        page.actions.type("\n")
                    else:
                        page.actions.key_down("CTRL").type("\n").key_up("CTRL")
                    self._sleep(1.2)
                    if self._looks_submitted(page):
                        return True
                except Exception:
                    continue
            self._nudge_input(page, input_ele)
        return False

    def _find_send_button(self, page: Any) -> Optional[Any]:
        selectors = list(self.settings.get("send_selectors", []) or [])
        selectors.extend(
            [
                'css:button[aria-label*="Send"]',
                'css:button[aria-label*="发送"]',
                'css:button[aria-label*="提交"]',
                'css:button[title*="Send"]',
                'css:button[title*="发送"]',
                'css:button[type="submit"]',
                'css:[data-testid*="send"]',
            ]
        )
        for selector in selectors:
            try:
                elements = page.eles(selector, timeout=1)
            except Exception:
                elements = []
            for ele in elements or []:
                if self._is_send_like(page, ele):
                    return ele
        return None

    def _click_send_button_by_js(self, page: Any) -> bool:
        try:
            return bool(
                page.run_js(
                    """
                    const words = ['send', '发送', '提交'];
                    const banned = ['voice', '语音', 'microphone', 'mic'];
                    const buttons = [...document.querySelectorAll('button,[role="button"]')];
                    const visible = el => {
                      const r = el.getBoundingClientRect();
                      return r.width > 0 && r.height > 0 && !el.disabled && getComputedStyle(el).visibility !== 'hidden';
                    };
                    const found = buttons.find(el => {
                      const text = [
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        el.innerText || '',
                        el.textContent || '',
                        el.getAttribute('data-testid') || ''
                      ].join(' ').toLowerCase();
                      return visible(el) && words.some(w => text.includes(w)) && !banned.some(w => text.includes(w));
                    });
                    if (!found) return false;
                    found.click();
                    return true;
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _is_send_like(page: Any, ele: Any) -> bool:
        parts = []
        for name in ("aria-label", "title", "data-testid"):
            try:
                value = ele.attr(name)
            except Exception:
                value = ""
            if value:
                parts.append(str(value))
        try:
            parts.append(ele.text or "")
        except Exception:
            pass
        text = " ".join(parts).lower()
        text = str(text or "").lower()
        return any(word in text for word in ("send", "发送", "提交", "submit")) and not any(
            word in text for word in ("voice", "语音", "microphone", "mic")
        )

    def _looks_submitted(self, page: Any) -> bool:
        try:
            length = int(
                page.run_js(
                    """
                    const el = document.activeElement || document.querySelector('textarea,[contenteditable="true"],[role="textbox"]');
                    if (!el) return 0;
                    const text = 'value' in el ? el.value : (el.innerText || el.textContent || '');
                    return (text || '').trim().length;
                    """
                )
                or 0
            )
            if length == 0:
                return True
        except Exception:
            pass
        try:
            body = page.ele("tag:body", timeout=1)
            text = (body.text or "").lower() if body else ""
            return any(marker in text for marker in ("thinking", "正在", "停止生成", "stop generating"))
        except Exception:
            return False

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

    def _wait_for_response(self, page: Any, response_validator: Optional[Callable[[str], bool]] = None) -> str:
        timeout = float(self.settings.get("response_timeout_seconds", 120))
        poll = float(self.settings.get("response_poll_seconds", 3))
        required_stable = max(2, int(self.settings.get("response_stable_polls", 4 if response_validator else 2) or 2))
        extra_after_valid = max(0, int(self.settings.get("response_valid_extra_polls", 1) or 0))
        end = time.time() + timeout
        last_text = ""
        stable_count = 0
        valid_count = 0
        while time.time() < end:
            text = self._extract_response_text(page)
            if text and text == last_text:
                stable_count += 1
            elif text:
                stable_count = 0
                last_text = text
                valid_count = 0
            if last_text and response_validator:
                try:
                    is_valid = bool(response_validator(last_text))
                except Exception:
                    is_valid = False
                valid_count = valid_count + 1 if is_valid else 0
                if is_valid and stable_count >= required_stable and valid_count > extra_after_valid:
                    return last_text
            elif last_text and stable_count >= required_stable:
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
