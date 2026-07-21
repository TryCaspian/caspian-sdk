"""comm - CLI for the communication gateway.

Commands:
  comm init [--gateway URL] [--name NAME]   mint a sandbox key, write .env
  comm connect email [--name NAME]          provision an email inbox
  comm status                               list connections
  comm listen                               tail inbound/outbound mail live
  comm test-email [TEXT]                    deliver a test email to your agent
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

DEFAULT_GATEWAY = "http://127.0.0.1:8000"
ENV_PATH = Path.cwd() / ".env"


def _dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _config() -> tuple[str, str]:
    env = {**_dotenv(), **os.environ}
    api_key = env.get("COMM_API_KEY")
    base_url = env.get("COMM_BASE_URL", DEFAULT_GATEWAY)
    if not api_key:
        sys.exit("No COMM_API_KEY found. Run: comm init --gateway <url>")
    return api_key, base_url


def _request(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None):
    api_key, base_url = _config()
    response = httpx.request(
        method,
        f"{base_url}{path}",
        json=json_body,
        params=params,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        sys.exit(f"Error {response.status_code}: {detail}")
    return response.json()


def _write_env(values: dict[str, str]) -> None:
    existing = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    keys = set(values)
    lines = [line for line in existing if line.split("=", 1)[0].strip() not in keys]
    lines.extend(f"{key}={value}" for key, value in values.items())
    ENV_PATH.write_text("\n".join(lines) + "\n")


def cmd_init(args) -> None:
    env = {**_dotenv(), **os.environ}
    if env.get("COMM_API_KEY") and not args.force:
        print("COMM_API_KEY already configured in .env (use --force to replace).")
        return
    gateway = args.gateway.rstrip("/")
    response = httpx.post(
        f"{gateway}/v1/projects/sandbox",
        json={"name": args.name},
        timeout=30,
    )
    if response.status_code >= 400:
        sys.exit(f"Error {response.status_code}: {response.text}")
    data = response.json()
    _write_env({"COMM_API_KEY": data["api_key"], "COMM_BASE_URL": gateway})
    print(f"Project {data['project_id']} created.")
    print(f"Wrote COMM_API_KEY and COMM_BASE_URL to {ENV_PATH}")
    print("Next: comm connect email")


# Pure-OAuth connect (returns an authorize_url straight from /connections/{ch}).
OAUTH_CHANNELS = {"instagram", "facebook"}
# Channels with a one-click /connections/{ch}/install endpoint AND a bring-your-own
# path — the CLI asks which the developer wants.
INSTALL_CHANNELS = {"slack", "discord", "github", "x"}
TOKEN_CHANNELS = {"telegram": "@BotFather"}


def _ask(prompt: str, default: str = "") -> str:
    """Prompt on a TTY; fall back to the default in non-interactive runs."""
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _live_channels() -> list[str]:
    try:
        return [c["channel"] for c in _request("GET", "/v1/channels")]
    except SystemExit:
        return ["email"]


def _pick_channel(requested: str | None) -> str:
    channels = _live_channels()
    if requested:
        if requested not in channels:
            joined = ", ".join(channels)
            sys.exit(f"Channel {requested!r} is not available. Live channels: {joined}")
        return requested
    if not sys.stdin.isatty():
        return channels[0]
    print("Available channels:")
    for i, ch in enumerate(channels, 1):
        note = {
            "email": "instant, no setup (default or your own domain)",
            "telegram": "needs a bot token from @BotFather",
            "discord": "one-click install, or bring your own bot",
            "slack": "one-click install, or bring your own app",
            "github": "one-click install, or bring your own GitHub App",
            "x": "one-click 'Sign in with X', or bring your own tokens",
            "whatsapp": "Caspian hosted",
            "imessage": "Caspian hosted",
            "instagram": "OAuth (your Meta app)", "facebook": "OAuth (your Meta app)",
        }.get(ch, "")
        print(f"  {i}. {ch}" + (f"  ({note})" if note else ""))
    choice = _ask("Which channel do you want to connect? (number or name)", "1")
    if choice.isdigit() and 1 <= int(choice) <= len(channels):
        return channels[int(choice) - 1]
    if choice in channels:
        return choice
    sys.exit(f"Unknown choice {choice!r}")


def _email_body(args) -> dict:
    body: dict = {"display_name": args.name}
    domain = args.domain
    username = args.username
    if domain is None and username is None and sys.stdin.isatty():
        which = _ask("Use the gateway's default domain or your own custom domain?",
                     "default").lower()
        if which.startswith("c"):
            domain = _ask("Your verified custom subdomain (e.g. agents.yourco.com)")
            if domain:
                username = _ask("Exact username for the address (blank = auto)")
    if domain:
        body["domain"] = domain
    if username:
        body["username"] = username
    return body


def _print_authorize(channel: str, connection: dict) -> None:
    print(f"\nOpen this link to authorize {channel} (it becomes your bot):")
    print(f"  {connection.get('authorize_url')}")
    print(f"After approving, run: comm status   (connection {connection['id']})")


def _await_active(connection: dict) -> None:
    deadline = time.monotonic() + 60
    while connection["status"] == "provisioning" and time.monotonic() < deadline:
        time.sleep(0.5)
        connection = _request("GET", f"/v1/connections/{connection['id']}")
    if connection["status"] != "active":
        sys.exit(f"Provisioning did not complete: {json.dumps(connection, indent=2)}")
    print(f"{connection['channel'].capitalize()} connected: {connection['address']}")
    print(f"Connection id: {connection['id']}")


def _connect_install_channel(channel: str, args) -> None:
    """Ask one-click install vs bring-your-own, then connect the channel."""
    quick = True
    if sys.stdin.isatty():
        kind = _ask(f"{channel}: (a) quick one-click install, or (b) bring your own?", "a")
        quick = not kind.lower().startswith("b")
    if quick:
        conn = _request("POST", f"/v1/connections/{channel}/install",
                        json_body={"display_name": args.name})
        _print_authorize(channel, conn)
        return
    # bring-your-own paths
    if channel == "discord":
        token = args.bot_token or _ask("Paste your bot token (discord.com/developers)")
        if not token:
            sys.exit("discord BYO needs a bot token.")
        _await_active(_request("POST", "/v1/connections/discord",
                     json_body={"display_name": args.name, "bot_token": token}))
    elif channel == "slack":
        conn = _request("POST", "/v1/connections/slack", json_body={
            "display_name": args.name,
            "slack_client_id": _ask("Slack client id"),
            "slack_client_secret": _ask("Slack client secret"),
            "slack_signing_secret": _ask("Slack signing secret")})
        _print_authorize("slack", conn)
    elif channel == "github":
        private_key_path = _ask("Path to GitHub App private key PEM")
        if not private_key_path:
            sys.exit("GitHub BYO needs a private key PEM.")
        try:
            private_key = Path(private_key_path).expanduser().read_text()
        except OSError as exc:
            sys.exit(f"Could not read GitHub private key: {exc}")
        conn = _request("POST", "/v1/connections/github", json_body={
            "display_name": args.name,
            "github_app_id": _ask("GitHub App id"),
            "github_app_slug": _ask("GitHub App slug"),
            "github_private_key": private_key,
            "github_webhook_secret": _ask("GitHub webhook secret"),
            "receive_mode": "mentions"})
        _print_authorize("github", conn)
    elif channel == "x":
        _await_active(_request("POST", "/v1/connections/x", json_body={
            "access_token": _ask("X access token"),
            "access_secret": _ask("X access token secret"),
            "user_id": _ask("X numeric user id (before the '-' in the access token)")}))


def _connect_one(channel: str, args) -> None:
    if channel in INSTALL_CHANNELS:
        _connect_install_channel(channel, args)
        return
    if channel == "email":
        body = _email_body(args)
    elif channel in TOKEN_CHANNELS:
        where = TOKEN_CHANNELS[channel]
        token = args.bot_token or _ask(f"Paste the bot token (create one at {where})")
        if not token:
            sys.exit(f"{channel} needs a bot token.")
        body = {"display_name": args.name, "bot_token": token}
    else:
        body = {"display_name": args.name}

    connection = _request("POST", f"/v1/connections/{channel}", json_body=body)
    if channel in OAUTH_CHANNELS:
        _print_authorize(channel, connection)
        return
    _await_active(connection)


def cmd_connect(args) -> None:
    _connect_one(_pick_channel(args.channel), args)
    while sys.stdin.isatty():
        again = _ask("Connect another channel?", "no").lower()
        if not again.startswith("y"):
            break
        _connect_one(_pick_channel(None), args)


def cmd_status(args) -> None:
    connections = _request("GET", "/v1/connections")
    if not connections:
        print("No connections. Run: comm connect email")
        return
    for c in connections:
        print(f"{c['id']}  {c['channel']:<6} {c['status']:<12} {c['address'] or '-'}")


def cmd_listen(args) -> None:
    seq = 0
    batch = _request("GET", "/v1/events", params={"after_seq": 0, "limit": 500})
    while batch:
        seq = batch[-1]["seq"]
        batch = _request("GET", "/v1/events", params={"after_seq": seq, "limit": 500})
    print("Listening for mail (Ctrl+C to stop)")
    while True:
        for event in _request("GET", "/v1/events", params={"after_seq": seq}):
            seq = event["seq"]
            data = event["data"]
            if event["type"] == "message.received":
                m = data["message"]
                sender = (m.get("sender") or {}).get("address", "?")
                preview = (m.get("text") or "").strip()[:120]
                print(f"<- {sender}: {m.get('subject')!r} | {preview!r}")
            elif event["type"] == "message.sent":
                m = data["message"]
                to = ", ".join(r["address"] for r in m.get("recipients", []))
                print(f"-> {to}: {(m.get('text') or '').strip()[:120]!r}")
            else:
                print(f"** {event['type']}")
        time.sleep(1.0)


def cmd_test_email(args) -> None:
    result = _request(
        "POST",
        "/v1/test-emails",
        json_body={"text": args.text, "subject": args.subject, "connection_id": args.connection},
    )
    print(f"Delivering test email to {result['to']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="comm", description="Communication gateway CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Mint a sandbox project and write .env")
    p_init.add_argument("--gateway", default=DEFAULT_GATEWAY)
    p_init.add_argument("--name", default="sandbox")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_connect = sub.add_parser(
        "connect", help="Connect a channel (interactive if you omit the channel)"
    )
    p_connect.add_argument(
        "channel",
        nargs="?",
        default=None,
        choices=[
            None, "email", "telegram", "phone", "whatsapp", "imessage", "rcs",
            "discord", "slack", "github", "x", "instagram", "facebook",
        ],
        help="Channel to connect; omit to be shown the live options and asked",
    )
    p_connect.add_argument("--name", default=None, help="Display name for the connection")
    p_connect.add_argument("--bot-token", default=None, help="Telegram bot token from @BotFather")
    p_connect.add_argument("--domain", default=None, help="Verified custom domain for the inbox")
    p_connect.add_argument(
        "--username", default=None, help="Exact local part, e.g. kernel (custom domains only)"
    )
    p_connect.set_defaults(func=cmd_connect)

    p_status = sub.add_parser("status", help="List connections")
    p_status.set_defaults(func=cmd_status)

    p_listen = sub.add_parser("listen", help="Tail mail events live")
    p_listen.set_defaults(func=cmd_listen)

    p_test = sub.add_parser("test-email", help="Deliver a test email to your agent")
    p_test.add_argument("text", nargs="?", default="Hello, are you alive?")
    p_test.add_argument("--subject", default="Test email")
    p_test.add_argument("--connection", default=None)
    p_test.set_defaults(func=cmd_test_email)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
