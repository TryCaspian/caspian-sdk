"""Voice responder — surfaces the TwiML a voice webhook must return.

Voice (Twilio Programmable Voice) is response-based, not push: the developer's
webhook must return a TwiML document in its HTTP response body rather than
making an outbound API call. So this "transport" does not send anything; it
simply lifts the TwiML string out of the adapter's request-description so the
webhook layer can return it. Non-twiml Sents pass through unchanged.
"""

from __future__ import annotations

from caspian.core.ports import Result, Sent


class VoiceResponder:
    """Handles "twiml" Sents by surfacing their TwiML document."""

    def dispatch(self, sent: Sent) -> Result:
        if sent.raw.get("transport") != "twiml":
            return Result.ok(sent)
        return Result.ok(
            Sent(raw={"native": "twiml", "twiml": sent.raw.get("twiml", "")})
        )


__all__ = ["VoiceResponder"]
