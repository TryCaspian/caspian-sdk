"""Small runtime shims so the SDK runs on the same Pythons as the published one.

enum.StrEnum arrived in 3.11. The published caspian-sdk supports 3.10, so
requiring 3.11 here would mean developers on 3.10 could not upgrade. The
fallback reproduces StrEnum's behaviour that this codebase relies on: members
compare equal to their string value, and str() gives the value rather than
"ClassName.MEMBER".
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # pragma: no cover - exercised on 3.10 in CI, not on the dev machine
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """str-valued Enum, matching enum.StrEnum on 3.11+."""

        def __str__(self) -> str:
            return str(self.value)


__all__ = ["StrEnum"]
