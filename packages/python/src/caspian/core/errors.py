"""Tagged failures: raise from add(); put on Result in handle/listen/poll.

Same objects either way. Core interpreters still return Result; they do not raise.
Match on `.tag` or `except ProvisionError`.
"""

from __future__ import annotations


class CaspianError(Exception):
    """Closed tagged failure. Match on `.tag` or except a subclass."""

    tag: str = ""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)


class DecodeError(CaspianError):
    tag = "DecodeError"


class AdapterError(CaspianError):
    tag = "AdapterError"

    def __init__(self, reason: str, command_tag: str = "") -> None:
        super().__init__(reason)
        self.command_tag = command_tag


class OverlapFull(CaspianError):
    tag = "OverlapFull"

    def __init__(self, thread_id: str, bound: int, reason: str = "") -> None:
        super().__init__(reason or f"overlap full for {thread_id} (bound {bound})")
        self.thread_id = thread_id
        self.bound = bound


class ProvisionError(CaspianError):
    tag = "ProvisionError"


class AuthRequired(CaspianError):
    tag = "AuthRequired"


class AccountRequired(CaspianError):
    tag = "AccountRequired"


class InsufficientCredit(CaspianError):
    tag = "InsufficientCredit"

    def __init__(self, reason: str = "", balance_cents: int = 0) -> None:
        super().__init__(reason)
        self.balance_cents = balance_cents


class RateLimited(CaspianError):
    tag = "RateLimited"

    def __init__(self, reason: str = "", retry_after_seconds: float = 0.0) -> None:
        super().__init__(reason)
        self.retry_after_seconds = retry_after_seconds


class GatewayError(CaspianError):
    tag = "GatewayError"

    def __init__(self, reason: str, status_code: int = 0) -> None:
        super().__init__(reason)
        self.status_code = status_code
