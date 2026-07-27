"""iMessage adapter on the managed-phone provider.

Twilio can't do iMessage (no Apple API); the managed-phone upstream can, via the
same messages API - it routes iMessage vs SMS based on the number and recipient.
Same client as the phone provider, just bound to the imessage channel. Requires
an iMessage-enabled number on the upstream account.
"""

from .caspian_phone import CaspianPhoneProvider


class CaspianPhoneIMessageProvider(CaspianPhoneProvider):
    name = "caspian-imessage"
    channel = "imessage"
