"""Persistent inbound listeners for channels that push over a long-lived
connection instead of an HTTP webhook (Discord Gateway; later telegram-user
MTProto, Slack Socket Mode).

`run_listeners` is launched in a background thread by the worker (and the inline
worker in dev); it holds one connection per active listener-channel connection
and feeds inbound messages into the same event pipeline as the webhook route.
"""

from .manager import run_listeners

__all__ = ["run_listeners"]
