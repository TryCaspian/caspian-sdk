"""caspian - CLI for the Caspian communication gateway.

Commands:
  caspian init [--gateway URL] [--open]        sign in (device login), write .env
  caspian init --sandbox [--name NAME]         mint an anonymous sandbox key (no sign-in)
  caspian connect email [--name NAME]          provision an email inbox
  caspian status                               list connections
  caspian listen                               tail inbound/outbound mail live
  caspian test-email [TEXT]                    deliver a test email to your agent
  caspian login                                sign in / bind current key to an account
  caspian billing                              show credit balance, spend, limits
  caspian topup [DOLLARS]                      add credit via a Stripe checkout link
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from . import telemetry

DEFAULT_GATEWAY = "https://api.trycaspianai.com"
DASHBOARD_URL = "https://dashboard.trycaspianai.com"
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


def _resolve(*keys: str, default: str | None = None) -> str | None:
    """Resolve a value per source (env, then ./.env), preferring the branded
    CASPIAN_* name over legacy COMM_*. Matches the SDK: a source is only consulted
    for a non-empty value, so an empty env var can't mask a real ./.env value."""
    dotenv = _dotenv()
    for source in (os.environ.get, dotenv.get):
        for key in keys:
            value = source(key)
            if value:
                return value
    return default


def _config() -> tuple[str, str]:
    api_key = _resolve("CASPIAN_API_KEY", "COMM_API_KEY")
    base_url = _resolve("CASPIAN_BASE_URL", "COMM_BASE_URL", default=DEFAULT_GATEWAY)
    if not api_key:
        sys.exit("No CASPIAN_API_KEY found. Run: caspian init --gateway <url>")
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
    _raise_for_error(response)
    return response.json()


def _raise_for_error(response: httpx.Response) -> None:
    """Map an unsuccessful gateway response to the CLI's user-facing errors."""
    if response.status_code >= 400:
        try:
            payload = response.json()
            detail = (
                payload.get("detail", response.text)
                if isinstance(payload, dict)
                else response.text
            )
        except ValueError:
            detail = response.text
        # A billing block (out of credit / spend cap) comes back as a structured
        # body - print a clear, actionable message instead of a raw dict.
        if isinstance(detail, dict) and detail.get("reason") in {
            "insufficient_credit", "monthly_cap_reached", "channel_cap_reached"
        }:
            _exit_out_of_credit(detail)
        # A paid channel used before the developer signed in.
        if isinstance(detail, dict) and detail.get("reason") == "account_required":
            print(f"\n{detail.get('message', 'Sign-in required for paid channels.')}",
                  file=sys.stderr)
            print("  Sign in once:  caspian login\n", file=sys.stderr)
            sys.exit(3)
        sys.exit(f"Error {response.status_code}: {detail}")


def _exit_out_of_credit(detail: dict) -> None:
    balance = detail.get("balance_cents")
    bal = f"${balance / 100:.2f}" if isinstance(balance, int) else "unknown"
    opts = detail.get("payment_options") or []
    dash = next((o.get("url") for o in opts if o.get("url")),
                "https://dashboard.trycaspianai.com")
    print("\nOut of Caspian credit - this paid channel is blocked.", file=sys.stderr)
    print(f"  {detail.get('message', '')}", file=sys.stderr)
    print(f"  Balance: {bal}", file=sys.stderr)
    print(f"  Add credit in the dashboard:  {dash}\n", file=sys.stderr)
    sys.exit(2)


def _write_env(values: dict[str, str]) -> None:
    existing = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    keys = set(values)
    lines = [line for line in existing if line.split("=", 1)[0].strip() not in keys]
    lines.extend(f"{key}={value}" for key, value in values.items())
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _device_login(
    gateway: str,
    *,
    api_key: str | None = None,
    open_browser: bool = False,
) -> dict:
    """Run RFC 8628 device login against ``gateway``. No prior key required.

    When ``api_key`` is set, the gateway binds that project to the account on
    approve (carry-over). Otherwise a fresh account project is created.
    Returns the approved token payload (api_key, project_id, email, ...).
    """
    gateway = gateway.rstrip("/")
    body = {"api_key": api_key} if api_key else {}
    response = httpx.post(f"{gateway}/v1/auth/device/start", json=body, timeout=30)
    if response.status_code >= 400:
        sys.exit(f"Error {response.status_code}: {response.text}")
    start = response.json()
    url = start.get("verification_uri_complete") or start.get("verification_uri")
    telemetry.track("cli.login_url_shown", {})
    print("Sign in to Caspian (opens a browser link — one-time Google sign-in):")
    print(f"\n  {url}\n")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    print("Waiting for you to approve in the browser...")
    interval = start.get("interval", 5)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        poll = httpx.post(
            f"{gateway}/v1/auth/device/token",
            json={"device_code": start["device_code"]},
            timeout=30,
        )
        if poll.status_code >= 400:
            telemetry.track("cli.login_failed", {"reason": f"http_{poll.status_code}"})
            sys.exit(f"Error {poll.status_code}: {poll.text}")
        result = poll.json()
        status = result.get("status")
        if status == "approved":
            telemetry.set_identity(
                email=result.get("email"),
                project_id=result.get("project_id"),
                api_key=result.get("api_key"),
            )
            telemetry.track("cli.login_approved", {})
            return result
        if status in ("expired", "not_found"):
            telemetry.track("cli.login_failed", {"reason": status})
            sys.exit(f"Login {status}. Run caspian init (or caspian login) again.")
        time.sleep(interval)
    telemetry.track("cli.login_failed", {"reason": "timeout"})
    sys.exit("Login timed out. Run caspian init (or caspian login) again.")


def _cmd_init_sandbox(args) -> None:
    """Anonymous sandbox mint — no sign-in. Used by ``caspian init --sandbox``."""
    gateway = args.gateway.rstrip("/")
    telemetry.set_gateway(gateway)
    telemetry.track("cli.init_started", {"sandbox": True})
    response = httpx.post(
        f"{gateway}/v1/projects/sandbox",
        json={"name": args.name},
        timeout=30,
    )
    if response.status_code >= 400:
        sys.exit(f"Error {response.status_code}: {response.text}")
    data = response.json()
    _write_env({"CASPIAN_API_KEY": data["api_key"], "CASPIAN_BASE_URL": gateway})
    telemetry.set_identity(project_id=data.get("project_id"), api_key=data["api_key"])
    print(f"Sandbox project {data['project_id']} created (anonymous — no account).")
    print(f"Wrote CASPIAN_API_KEY and CASPIAN_BASE_URL to {ENV_PATH}")
    print("Next: caspian connect email")
    print("Tip: run caspian login later to tie this project to your account.")


def cmd_init(args) -> None:
    if _resolve("CASPIAN_API_KEY", "COMM_API_KEY") and not args.force:
        print("CASPIAN_API_KEY already configured in .env (use --force to replace).")
        return
    if args.sandbox:
        _cmd_init_sandbox(args)
        return

    gateway = args.gateway.rstrip("/")
    telemetry.set_gateway(gateway)
    telemetry.track("cli.init_started", {"sandbox": False})
    result = _device_login(gateway, open_browser=args.open)
    api_key = result.get("api_key")
    if not api_key:
        sys.exit("Sign-in succeeded but no API key was returned.")
    _write_env({"CASPIAN_API_KEY": api_key, "CASPIAN_BASE_URL": gateway})
    email = result.get("email") or "your account"
    project_id = result.get("project_id") or "?"
    telemetry.set_identity(email=result.get("email"), project_id=result.get("project_id"),
                           api_key=api_key)
    print(f"\nSigned in as {email}.")
    print(f"Project {project_id} ready.")
    print(f"Wrote CASPIAN_API_KEY and CASPIAN_BASE_URL to {ENV_PATH}")
    print("Next: caspian connect email")


def cmd_domains(args) -> None:
    if args.action == "add":
        domain = _request("POST", "/v1/domains", json_body={"domain": args.domain})
        print(f"Domain {domain['domain']} registered ({domain['status']}).")
        print("Add these DNS records at your registrar:")
        for record in domain["dns_records"]:
            priority = f" {record['priority']}" if record.get("priority") else ""
            print(f"  {record['type']:<6} {record['name']}  ->{priority} {record['value']}")
        print(f"Zone file: caspian domains zone-file {domain['id']}")
        print(f"Check status: caspian domains status {domain['id']}")
    elif args.action == "list":
        for domain in _request("GET", "/v1/domains"):
            print(f"{domain['id']}  {domain['status']:<12} {domain['domain']}")
    elif args.action == "status":
        domain = _request("GET", f"/v1/domains/{args.domain}")
        print(f"{domain['domain']}: {domain['status']}")
    elif args.action == "zone-file":
        api_key, base_url = _config()
        response = httpx.get(
            f"{base_url}/v1/domains/{args.domain}/zone-file",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        _raise_for_error(response)
        print(response.text)


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
    telemetry.track("cli.connect_authorize_shown", {"channel": channel})
    print(f"\nOpen this link to authorize {channel} (it becomes your bot):")
    print(f"  {connection.get('authorize_url')}")
    print(f"After approving, run: caspian status   (connection {connection['id']})")


def _await_active(connection: dict) -> None:
    channel = connection.get("channel") or "unknown"
    deadline = time.monotonic() + 60
    while connection["status"] == "provisioning" and time.monotonic() < deadline:
        time.sleep(0.5)
        connection = _request("GET", f"/v1/connections/{connection['id']}")
    if connection["status"] != "active":
        telemetry.track("cli.connect_failed", {
            "channel": channel, "reason": connection.get("status") or "timeout",
        })
        sys.exit(f"Provisioning did not complete: {json.dumps(connection, indent=2)}")
    telemetry.track("cli.connect_succeeded", {"channel": channel})
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
        return  # OAuth finish is outside this process
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
    telemetry.track("cli.connect_started", {"channel": channel})
    try:
        if channel in INSTALL_CHANNELS:
            _connect_install_channel(channel, args)
            # Install/OAuth paths end at authorize URL; success is later via status.
            return
        if channel == "email":
            body = _email_body(args)
        elif channel in TOKEN_CHANNELS:
            where = TOKEN_CHANNELS[channel]
            token = args.bot_token or _ask(f"Paste the bot token (create one at {where})")
            if not token:
                telemetry.track("cli.connect_failed", {
                    "channel": channel, "reason": "missing_bot_token",
                })
                sys.exit(f"{channel} needs a bot token.")
            body = {"display_name": args.name, "bot_token": token}
        elif channel == "bluesky":
            body = {
                "display_name": args.name,
                "identifier": _ask("Bluesky handle or email (e.g. myagent.bsky.social)"),
                "app_password": _ask(
                    "Bluesky app password (bsky.app -> Settings -> Privacy and "
                    "security -> App passwords)"
                ),
            }
        else:
            body = {"display_name": args.name}

        connection = _request("POST", f"/v1/connections/{channel}", json_body=body)
        if channel in OAUTH_CHANNELS:
            _print_authorize(channel, connection)
            return
        _await_active(connection)
    except SystemExit:
        raise
    except Exception as exc:
        telemetry.track("cli.connect_failed", {
            "channel": channel, "reason": type(exc).__name__,
        })
        raise


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
        print("No connections. Run: caspian connect email")
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


def cmd_login(args) -> None:
    """One-time developer sign-in that ties this project to a Caspian account.
    Works with or without an existing key — if a sandbox key is present it is
    carried over; otherwise a fresh account project is created."""
    api_key = _resolve("CASPIAN_API_KEY", "COMM_API_KEY")
    base_url = _resolve(
        "CASPIAN_BASE_URL", "COMM_BASE_URL", default=DEFAULT_GATEWAY
    ).rstrip("/")
    telemetry.set_gateway(base_url)
    if api_key:
        telemetry.set_identity(api_key=api_key)
    result = _device_login(base_url, api_key=api_key, open_browser=args.open)
    # Prefer the account key from the token response (dashboard-first accounts
    # may differ from the anonymous sandbox key that started the flow).
    new_key = result.get("api_key")
    if new_key:
        _write_env({"CASPIAN_API_KEY": new_key, "CASPIAN_BASE_URL": base_url})
    email = result.get("email")
    print("\nSigned in. This project is now tied to your account"
          + (f" ({email})." if email else "."))
    print(f"Next: add credit in the dashboard:  {DASHBOARD_URL}")
    print("Then: caspian connect email")


def _fmt_cents(cents) -> str:
    return f"${cents / 100:.2f}" if isinstance(cents, int) else "-"


def cmd_billing(args) -> None:
    b = _request("GET", "/v1/billing")
    print(f"Balance:        {_fmt_cents(b['balance_cents'])}")
    print(f"Credit added:   {_fmt_cents(b['credit_cents'])}")
    print(f"Spent (total):  {_fmt_cents(b['spent_cents'])}")
    print(f"Spent (month):  {_fmt_cents(b['spent_this_month_cents'])}")
    print(f"Paid channels:  {', '.join(b['paid_channels'])}")
    limits = b.get("limits", {})
    monthly = limits.get("monthly_cap_cents")
    print(f"Monthly cap:    {_fmt_cents(monthly) if monthly else 'none'}")
    if limits.get("channel_caps"):
        caps = ", ".join(f"{k}={_fmt_cents(v)}" for k, v in limits["channel_caps"].items())
        print(f"Channel caps:   {caps}")
    ap = b.get("autopay", {})
    if ap.get("enabled"):
        print(f"Autopay:        on (refill {_fmt_cents(ap.get('topup_cents'))} below "
              f"{_fmt_cents(ap.get('threshold_cents'))})")
    else:
        print("Autopay:        off")
    if b["balance_cents"] <= 0:
        print(f"\nYou're out of credit. Add credit in the dashboard:  {DASHBOARD_URL}")


def cmd_topup(args) -> None:
    cents = args.amount_cents if args.amount_cents is not None else int(round(args.amount * 100))
    result = _request("POST", "/v1/billing/topup", json_body={"amount_cents": cents})
    url = result["checkout_url"]
    print(f"Add {_fmt_cents(cents)} of credit - pay here:\n\n  {url}\n")
    print(result.get("note", ""))
    if args.open:
        import webbrowser
        webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="caspian", description="Caspian communication CLI")
    parser.add_argument(
        "--no-telemetry",
        action="store_true",
        help="Disable CLI analytics (also: CASPIAN_TELEMETRY=0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="Sign in and write .env (use --sandbox for anonymous key, no sign-in)",
    )
    p_init.add_argument("--gateway", default=DEFAULT_GATEWAY)
    p_init.add_argument(
        "--sandbox",
        action="store_true",
        help="Mint an anonymous sandbox key without signing in (agents/tests)",
    )
    p_init.add_argument(
        "--name", default="sandbox", help="Project name when using --sandbox"
    )
    p_init.add_argument(
        "--open", action="store_true", help="Open the sign-in link in a browser"
    )
    p_init.add_argument("--force", action="store_true", help="Replace an existing .env key")
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

    p_domains = sub.add_parser("domains", help="Manage custom email domains")
    p_domains.add_argument("action", choices=["add", "list", "status", "zone-file"])
    p_domains.add_argument("domain", nargs="?", help="Domain name (add) or domain id")
    p_domains.set_defaults(func=cmd_domains)

    p_status = sub.add_parser("status", help="List connections")
    p_status.set_defaults(func=cmd_status)

    p_listen = sub.add_parser("listen", help="Tail mail events live")
    p_listen.set_defaults(func=cmd_listen)

    p_test = sub.add_parser("test-email", help="Deliver a test email to your agent")
    p_test.add_argument("text", nargs="?", default="Hello, are you alive?")
    p_test.add_argument("--subject", default="Test email")
    p_test.add_argument("--connection", default=None)
    p_test.set_defaults(func=cmd_test_email)

    p_login = sub.add_parser("login", help="Sign in once (enables paid channels)")
    p_login.add_argument("--open", action="store_true", help="Open the sign-in link in a browser")
    p_login.set_defaults(func=cmd_login)

    p_billing = sub.add_parser("billing", help="Show credit balance, spend, and limits")
    p_billing.set_defaults(func=cmd_billing)

    p_topup = sub.add_parser("topup", help="Add credit (opens a Stripe checkout link)")
    p_topup.add_argument(
        "amount", type=float, nargs="?", default=20.0, help="Dollars to add (default 20)"
    )
    p_topup.add_argument(
        "--cents", dest="amount_cents", type=int, default=None,
        help="Exact amount in cents (overrides the dollar amount)",
    )
    p_topup.add_argument("--open", action="store_true", help="Open the checkout link in a browser")
    p_topup.set_defaults(func=cmd_topup)

    args = parser.parse_args()
    gateway = getattr(args, "gateway", None) or _resolve(
        "CASPIAN_BASE_URL", "COMM_BASE_URL", default=DEFAULT_GATEWAY
    )
    api_key = _resolve("CASPIAN_API_KEY", "COMM_API_KEY")
    telemetry.configure(
        disabled=bool(getattr(args, "no_telemetry", False)),
        gateway=gateway,
        api_key=api_key,
    )
    telemetry.track("cli.session_started", {"command": args.command or ""})
    started = time.monotonic()
    flags = telemetry.argv_flags(args)
    telemetry.track("cli.command_started", {
        "command": args.command or "",
        "argv_flags": flags,
    })
    exit_code = 0
    try:
        args.func(args)
    except KeyboardInterrupt:
        exit_code = 130
        telemetry.track("cli.command_failed", {
            "command": args.command or "",
            "error_code": exit_code,
            "reason": "interrupted",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "argv_flags": flags,
        })
    except SystemExit as exc:
        code = exc.code
        exit_code = 0 if code in (None, 0) else (code if isinstance(code, int) else 1)
        props = {
            "command": args.command or "",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "argv_flags": flags,
        }
        if exit_code == 0:
            telemetry.track("cli.command_succeeded", props)
        else:
            props["error_code"] = exit_code
            telemetry.track("cli.command_failed", props)
        raise
    except Exception as exc:
        exit_code = 1
        telemetry.track("cli.command_failed", {
            "command": args.command or "",
            "error_code": exit_code,
            "reason": type(exc).__name__,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "argv_flags": flags,
        })
        raise
    else:
        telemetry.track("cli.command_succeeded", {
            "command": args.command or "",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "argv_flags": flags,
        })
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
