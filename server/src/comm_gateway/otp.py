"""Extract a one-time verification code from an inbound SMS body.

Only used on connections whose manifest grants Capability.OTP (a real-mobile
line that actually receives third-party codes). Deliberately conservative: a
numeric run is treated as a code only when a cue word ("code", "OTP", ...) sits
near it, when it is Google's "G-123456" form, or when the whole message is just
the digits — so ordinary numbers in a normal text (prices, times, phone numbers)
are not misread as codes.
"""

import re

_PHONE = re.compile(r"\+?\d[\d\s().-]{9,}\d")
_GOOGLE = re.compile(r"\bG-(\d{4,8})\b")
_CANDIDATE = re.compile(r"\d[\d \-]{2,}\d")
_CUE = re.compile(r"code|otp|passcode|pin|verification|verify|one[-\s]?time", re.IGNORECASE)


def extract_otp_code(text: str | None) -> str | None:
    if not text:
        return None
    # Remove phone-number-looking runs so they can't be mistaken for a code.
    cleaned = _PHONE.sub(" ", text)

    google = _GOOGLE.search(cleaned)
    if google:
        return google.group(1)

    candidates = []
    for match in _CANDIDATE.finditer(cleaned):
        digits = re.sub(r"\D", "", match.group())
        if 4 <= len(digits) <= 8:
            candidates.append((match.start(), match.end(), digits))
    if not candidates:
        return None

    # Prefer a candidate with a cue word nearby (either side).
    for start, end, digits in candidates:
        if _CUE.search(cleaned[max(0, start - 25) : end + 25]):
            return digits

    # No cue word: only trust it when the whole message is the code.
    if len(candidates) == 1 and re.fullmatch(r"\d{4,8}", cleaned.strip()):
        return candidates[0][2]
    return None
