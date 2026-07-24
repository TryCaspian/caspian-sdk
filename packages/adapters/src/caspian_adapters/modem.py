"""Real-mobile-line phone provider over a GSM/LTE USB modem.

This is how an agent gets a number that actually receives third-party OTPs
(Google, WhatsApp, banks). Those senders reject VoIP/CPaaS line types, so the
number must be a genuine carrier SIM. The minimum hardware is one USB LTE dongle
+ a retail prepaid SIM plugged into any always-on machine (a Mac mini works);
add dongles for more numbers. The SIM's own MSISDN is the agent's number.

Sending is AT+CMGS over the modem's serial port. Inbound is NOT a webhook — a
modem delivers SMS to the SIM, read via an AT+CMGL / +CMTI poll loop. That poll
listener is a separate process (out of scope for this slice, same as the
telegram-user MTProto listener); the fake provider covers the gateway wiring and
the OTP-extraction path end to end.

pyserial is imported lazily so the package works without a modem attached.
"""

from collections.abc import Mapping

from .base import (
    Capability,
    InboundMessage,
    OutboundMessage,
    ProvisionRequest,
    ProvisionResult,
    SendResult,
    split_composite_id,
)


class GsmModemProvider:
    """GsmModemProvider implementation."""

    name = "gsm-modem"
    channel = "phone"
    # A real carrier SIM: cold-starts (INITIATE) and — the reason it exists —
    # receives third-party verification codes (OTP), which VoIP numbers can't.
    capabilities = frozenset(
        {
            Capability.RECEIVE,
            Capability.REPLY,
            Capability.SEND,
            Capability.INITIATE,
            Capability.OTP,
        }
    )

    def __init__(self, serial_port: str, msisdn: str, baud: int = 115200) -> None:
        """Execute __init__."""
        if not serial_port or not msisdn:
            raise ValueError(
                "COMM_MODEM_SERIAL_PORT and COMM_MODEM_MSISDN are required "
                "for the gsm-modem provider"
            )
        self._port = serial_port
        self._msisdn = msisdn
        self._baud = baud
        self._serial = None  # lazily opened pyserial handle

    def _conn(self):
        if self._serial is None:
            import serial  # noqa: PLC0415

            self._serial = serial.Serial(self._port, self._baud, timeout=5)
        return self._serial

    def _send_sms(self, to_number: str, text: str) -> SendResult:
        ser = self._conn()
        ser.write(b"AT+CMGF=1\r")  # text mode
        ser.write(f'AT+CMGS="{to_number}"\r'.encode())
        ser.write(text.encode() + b"\x1a")  # message + Ctrl-Z to send
        # The modem echoes "+CMGS: <mr>" with a message reference; a full impl
        # parses it. We key our id on the recipient so replies can route.
        return SendResult(provider_message_id=f"{to_number}:out", provider_thread_id=to_number)

    def provision(self, request: ProvisionRequest) -> ProvisionResult:
        """Execute provision."""
        return ProvisionResult(address=self._msisdn, provider_resource_id=self._msisdn)

    def send(
        self, provider_inbox_id: str, message: OutboundMessage, credentials=None
    ) -> SendResult:
        """Execute send."""
        return self._send_sms(message.to[0], message.text or "")

    def reply(
        self,
        provider_inbox_id: str,
        provider_message_id: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        """Execute reply."""
        remote_number, _ = split_composite_id(provider_message_id)
        return self._send_sms(remote_number, message.text or "")

    def initiate(
        self,
        provider_inbox_id: str,
        recipient: str,
        message: OutboundMessage,
        credentials=None,
    ) -> SendResult:
        """Execute initiate."""
        return self._send_sms(recipient, message.text or "")

    def parse_webhook(
        self,
        payload: bytes,
        headers: Mapping[str, str],
        credentials=None,
    ) -> list[InboundMessage]:
        # A modem has no webhook; inbound comes from the AT poll listener.

        """Execute parse_webhook."""
        raise NotImplementedError("gsm-modem inbound arrives via the AT poll loop, not webhooks")
