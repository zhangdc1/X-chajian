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

    def validate_once(self) -> LicenseResult:
        if not self.card_number:
            return LicenseResult(False, "missing card_number in automation config")
        try:
            from fnkuaiyan_go_based import FnKuaiYanGoBasedAPI

            self.api = FnKuaiYanGoBasedAPI()
            token_result = self.api.get_token()
            if token_result != "ok":
                return LicenseResult(False, f"token failed: {token_result}")
            login_result = self.api.card_login(self.card_number, self.app_version)
            if login_result.startswith("ok|"):
                return LicenseResult(True, login_result)
            return LicenseResult(False, login_result)
        except Exception as exc:
            return LicenseResult(False, str(exc))

    def heartbeat(self) -> LicenseResult:
        if self.api is None:
            return LicenseResult(False, "license api is not initialized")
        try:
            result = self.api.heartbeat()
            if result.startswith("ok|"):
                return LicenseResult(True, result)
            return LicenseResult(False, result)
        except Exception as exc:
            return LicenseResult(False, str(exc))

