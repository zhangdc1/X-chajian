import contextlib
import io
from dataclasses import dataclass
from typing import Optional


@dataclass
class LicenseResult:
    ok: bool
    message: str


class LicenseGuard:
    """Thin adapter around the existing card-key API.

    The worker can use the same card-key system as the GUI without importing or
    launching the GUI. Keeping this adapter small makes it easier to replace
    with a stronger packaged licensing layer later.
    """

    def __init__(self, card_number: Optional[str], app_version: str = "1.0.0"):
        self.card_number = (card_number or "").strip()
        self.app_version = app_version
        self.api = None

    @staticmethod
    def clean_message(message: object) -> str:
        text = str(message or "")
        replacements = {
            "\u2713": "ok",
            "\u2717": "x",
            "\u2705": "ok",
            "\u274c": "x",
            "\u26a0": "!",
            "\ufe0f": "",
            "\U0001f510": "",
            "\U0001f4e1": "",
            "\U0001f4e4": "",
            "\U0001f4e5": "",
            "\U0001f513": "",
            "\U0001f50d": "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.strip()

    @staticmethod
    def quiet_call(func, *args, **kwargs):
        # The card-key SDK prints emoji/status symbols. Packaged console apps on
        # some Windows machines use GBK and can fail while printing those logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)

    def validate_once(self) -> LicenseResult:
        if not self.card_number:
            return LicenseResult(False, "missing card_number in automation config")
        try:
            from fnkuaiyan_go_based import FnKuaiYanGoBasedAPI

            self.api = self.quiet_call(FnKuaiYanGoBasedAPI)
            token_result = self.clean_message(self.quiet_call(self.api.get_token))
            if token_result != "ok":
                return LicenseResult(False, f"token failed: {token_result}")
            login_result = self.clean_message(
                self.quiet_call(self.api.card_login, self.card_number, self.app_version)
            )
            if login_result.startswith("ok|"):
                return LicenseResult(True, login_result)
            return LicenseResult(False, login_result)
        except Exception as exc:
            return LicenseResult(False, self.clean_message(exc))

    def heartbeat(self) -> LicenseResult:
        if self.api is None:
            return LicenseResult(False, "license api is not initialized")
        try:
            result = self.clean_message(self.quiet_call(self.api.heartbeat))
            if result.startswith("ok|"):
                return LicenseResult(True, result)
            return LicenseResult(False, result)
        except Exception as exc:
            return LicenseResult(False, self.clean_message(exc))
