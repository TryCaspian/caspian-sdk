"""Argv → Intent. Pure. No HTTP.

Rejected duplicate send/follow paths exit with the one command to use.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from caspian_cli.intent import (
    Call,
    CatalogGet,
    CatalogList,
    CatalogSearch,
    ChannelsAdd,
    ChannelsLs,
    Intent,
    ThreadsLs,
    ThreadsTail,
)

_NOUNS = frozenset({"channels", "call", "catalog", "threads", "login", "init", "help"})


def parse_argv(argv: Sequence[str]) -> Intent:
    argv = list(argv)
    if not argv:
        raise SystemExit("usage: caspian <channels|call|catalog|threads|login|init>")

    head = argv[0]
    if head == "connect":
        raise SystemExit("use: caspian channels add")
    if head == "telegram" or (head not in _NOUNS and "." not in head):
        # `caspian telegram send-photo` and other per-channel programs.
        if head not in _NOUNS:
            raise SystemExit("use: caspian call <id>  (caspian catalog search …)")
    if head == "channels" and len(argv) > 1 and argv[1] == "watch":
        raise SystemExit("use: caspian threads tail")
    if head == "threads" and len(argv) > 1 and argv[1] == "reply":
        raise SystemExit("use: caspian call post --thread … --text …")

    parser = _parser()
    args = parser.parse_args(argv)
    return _to_intent(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="caspian")
    sub = parser.add_subparsers(dest="noun", required=True)

    channels = sub.add_parser("channels")
    ch = channels.add_subparsers(dest="verb", required=True)
    add = ch.add_parser("add")
    add.add_argument("channel")
    add.add_argument("--via", choices=("hosted", "self-host"), default="hosted")
    add.add_argument("--name", dest="display_name", default="")
    add.add_argument("--bot-token", dest="bot_token", default="")
    add.add_argument("--webhook-url", dest="webhook_url", default="")
    add.add_argument("--inbound", dest="inbound", action="store_true", default=True)
    add.add_argument("--no-inbound", dest="inbound", action="store_false")
    ch.add_parser("ls")

    call = sub.add_parser("call")
    call.add_argument("id")
    call.add_argument("--thread", dest="thread_id", default="")
    call.add_argument("--text", default="")
    call.add_argument("--file", default="")

    catalog = sub.add_parser("catalog")
    cat = catalog.add_subparsers(dest="verb")
    search = cat.add_parser("search")
    search.add_argument("query")
    get = cat.add_parser("get")
    get.add_argument("id")

    threads = sub.add_parser("threads")
    th = threads.add_subparsers(dest="verb", required=True)
    ls = th.add_parser("ls")
    ls.add_argument("--channel", default="")
    tail = th.add_parser("tail")
    tail.add_argument("thread_id", nargs="?", default="")

    sub.add_parser("login")
    sub.add_parser("init")
    return parser


def _to_intent(args: argparse.Namespace) -> Intent:
    noun = args.noun
    if noun == "channels":
        if args.verb == "add":
            return ChannelsAdd(
                channel=args.channel,
                via=args.via,
                display_name=args.display_name,
                bot_token=args.bot_token,
                webhook_url=args.webhook_url,
                inbound=args.inbound,
            )
        return ChannelsLs()
    if noun == "call":
        call_args: dict = {}
        if args.thread_id:
            call_args["thread_id"] = args.thread_id
        if args.text:
            call_args["text"] = args.text
        if args.file:
            call_args["file"] = args.file
        return Call(id=args.id, args=call_args)
    if noun == "catalog":
        verb = getattr(args, "verb", None)
        if verb == "search":
            return CatalogSearch(query=args.query)
        if verb == "get":
            return CatalogGet(id=args.id)
        return CatalogList()
    if noun == "threads":
        if args.verb == "ls":
            return ThreadsLs(channel=args.channel)
        return ThreadsTail(thread_id=args.thread_id)
    raise SystemExit(f"use: caspian channels|call|catalog|threads  (not {noun})")


def main(argv: Sequence[str] | None = None) -> Intent:
    return parse_argv(sys.argv[1:] if argv is None else argv)
