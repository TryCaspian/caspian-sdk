"""Serve SKILL.md - the agent-readable integration guide.

Coding agents (Claude Code, Codex, Cursor) fetch this and perform the whole
integration on the developer's behalf. Keep it imperative, exact, and short.
"""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter()

REPO = "https://github.com/TryCaspian/caspian-sdk"

SKILL_TEMPLATE = """# caspian-sdk: give an AI agent real communication channels

You are integrating messaging into an AI agent using caspian-sdk. It gives an
agent a real presence on many channels behind ONE `on_message` handler and one
`message.reply()` - the same code answers on every channel. The gateway is
hosted at {base}.

## How to drive this (read first)

The developer may just say "add caspian-sdk" without naming channels. Do this:

1. Do steps 1-3 below (key, install, the shared handler) - always needed.
2. Discover which channels are actually connectable RIGHT NOW:
   `curl -s {base}/v1/channels -H "Authorization: Bearer $CASPIAN_API_KEY"`
   That returns the live channels (currently: email, discord, slack, x,
   telegram, phone/SMS). Phone/SMS is BRING-YOUR-OWN (the developer's own
   Twilio/Telnyx number - see that section).
   Only offer those. Other channels (WhatsApp, Voice, RCS, Instagram, Facebook,
   iMessage) exist in the SDK but are NOT live on this gateway yet - do NOT try
   to connect them (they 400). If a developer asks for one of those, tell them
   it's "coming soon / being integrated" - don't attempt it and don't
   apologize at length; just say it's on the way. A channel may occasionally
   appear MORE THAN ONCE - each row is a different `provider` for that channel;
   when that happens, ask which provider they want and pass it as `provider=`
   to `connect_*()`.
3. ASK the developer which of the live channels they want (email needs nothing;
   others need a token or an OAuth click - see the per-channel sections). Don't
   assume; list the live options and let them choose. Email is a safe default
   if they don't care.
4. Connect each chosen channel (sections below), then verify with a test.

Every channel uses the SAME handler from step 3 - adding one is just another
`client.connect_*()` call, never new handler code.

## 1. Get an API key

If `CASPIAN_API_KEY` is already in the project's `.env`, SKIP this step.

**Free channels need NO signup.** Email, Telegram, Discord, Slack, Instagram,
Facebook, and the developer's own Twilio/Telnyx number cost nothing — just mint an
instant key and start building:

```bash
curl -s -X POST {base}/v1/projects/sandbox \\
  -H 'Content-Type: application/json' -d '{{"name":"my-agent"}}'
```

Returns `{{"project_id":"...","api_key":"comm_sandbox_..."}}`. Write it to `.env`:

```
CASPIAN_API_KEY=<api_key>
CASPIAN_BASE_URL={base}
```

**Free channels need no sign-in. Paid channels need a one-time sign-in, then credit.**
Free channels (email, Telegram, Discord, Slack) work instantly with just the API
key. Channels on Caspian's paid network (X, WhatsApp, iMessage) are gated on two
things, both surfaced as machine-actionable JSON:

**a) One-time developer sign-in (401 `account_required`).** The first paid action
returns **401** with `login_options`. Paid spend must be tied to a real account.
Get the developer signed in once (they never sign in again after this):

1. `POST {base}/v1/auth/device/start` with `{{"api_key": "<your key>"}}` (so THIS
   project binds to their account - nothing you built is lost).
2. SHOW the developer `verification_uri_complete` and WAIT for them to sign in.
3. Poll `POST {base}/v1/auth/device/token` with `{{"device_code": ...}}` until
   `status` is `approved`, then retry the call.

**b) Prepaid credit (402 `insufficient_credit`).** Once signed in, a paid action
returns **402** with `payment_options` if the balance is too low:

1. Check the balance any time: `GET {base}/v1/billing` (Bearer auth).
2. The developer adds credit in the dashboard. SHOW them this link and WAIT:

> Add credit to keep {{channel}} running: {dashboard}

3. Credit lands seconds after payment - poll `GET {base}/v1/billing` until
   `balance_cents` rises (or watch for the `billing.credited` event), then retry
   the call that 402'd.

**Autopay + spend limits (set them, they protect the developer):** after one
checkout the card is saved automatically. The agent can then enable
replenishment and caps over the API - a monthly cap is REQUIRED for autopay:

```bash
curl -s -X PUT {base}/v1/billing/autopay -H "Authorization: Bearer $CASPIAN_API_KEY" \\
  -H 'Content-Type: application/json' \\
  -d '{{"threshold_cents":500,"topup_cents":2000,"monthly_cap_cents":10000}}'
```

`PUT {base}/v1/billing/limits` sets `monthly_cap_cents` / per-channel
`channel_caps`. Watch for `billing.low_balance` and `billing.limit_reached`
events on the normal event stream and tell the developer when they fire.

## 2. Install the SDK

```bash
uv add caspian-sdk
# or: pip install caspian-sdk
```

## 3. Integrate - one handler for every channel

The SDK reads CASPIAN_API_KEY and CASPIAN_BASE_URL from the environment or ./.env
automatically. Write the handler ONCE; it fires for whatever channels you
connect below.

```python
from caspian_sdk import CommClient

client = CommClient()

# Connect the channels the developer chose (see the per-channel sections).
# Email needs nothing and is the safe default:
email = client.connect_email(username="scout")   # ASK the developer for the name
print("Agent email:", email["address"])          # e.g. scout@{email_domain}
# client.connect_telegram(bot_token=...)   # if they want Telegram
# client.connect_discord(bot_token=...)    # if they want Discord

@client.on_message
def handle(message):
    # Same for every channel: message.text, message.sender, message.conversation_id
    # (conversation_id is stable per thread - key your agent's memory on it)
    answer = your_agent_logic(message.text)
    message.reply(answer)   # replies on whichever channel the message arrived from

client.listen()
```

Wire `your_agent_logic` to whatever the developer's agent framework is
(OpenAI Agents SDK, LangGraph, plain LLM call). The reply must be plain text.

### Make the agent platform-aware (optional, recommended)

Each channel behaves differently - Slack threads, WhatsApp's 24h window, SMS
length limits, iMessage has no markdown, X caps public replies at 280. Caspian
returns concise etiquette for the channels the developer connected; inject it into
the agent's system prompt so replies fit each platform:

```python
guide = client.behavior_prompt()   # combined guide for connected channels
system_prompt = system_prompt + "\\n\\n" + guide
```

On any SDK version you can fetch it directly instead:
`curl -s {base}/v1/behavior-prompt -H "Authorization: Bearer $CASPIAN_API_KEY"`
(or one channel: `{base}/v1/channels/slack/guide`). It's a suggestion - the
developer can use it as-is, tweak it, or write their own. Offer it; don't force it.

STOP before connecting email - you MUST ask the developer what mailbox NAME they
want (the part before the @) and WAIT for their answer. Do NOT call a bare
`connect_email()` - that auto-generates an ugly random name like
scout-a3f9@... and is almost never what they want. Once they tell you the name:
`client.connect_email(username="<their choice>")` -> <choice>@{email_domain}.
If that name is taken the API replies 409 with a `suggestions` list of readable
alternatives - show those and let them pick one. If they'd rather use their OWN
domain, follow the custom-domain section and pass `domain=` too (e.g.
username="kernel", domain="agents.acme.com" -> kernel@agents.acme.com).

## 4. Verify it works

With the integration running in one terminal, deliver a test email:

```bash
curl -s -X POST {base}/v1/test-emails -H "Authorization: Bearer $CASPIAN_API_KEY" \\
  -H 'Content-Type: application/json' -d '{{"text":"hello, are you alive?"}}'
```

Within ~10 seconds the running integration must print the inbound message and
send a reply. Confirm by checking events:

```bash
curl -s "{base}/v1/events?type=message.sent" -H "Authorization: Bearer $CASPIAN_API_KEY"
```

If a `message.sent` event appears, the integration is complete. Tell the
developer their agent's email address; real emails to it from any mail client
now reach the handler.

If the developer sends a REAL email and says no reply arrived, FIRST tell them to
check their SPAM/Junk folder. A `message.sent` event (above) means the reply WAS
sent - a brand-new sending domain has no reputation yet, so Gmail/Outlook often
file the first replies as spam. Ask them to open it and click "Not spam"; that
trains the inbox and later replies land normally. This is deliverability warmup,
not a bug. Only if there's no `message.sent` event is something actually wrong.

## Custom email domain (optional)

By default addresses live on the platform domain. If the developer wants their
own brand (e.g. support-x@agents.acme.com), ask them:

> Do you want agent email addresses on your own domain? If yes, tell me a
> dedicated subdomain of a domain you control (e.g. agents.yourcompany.com).
> Do not use your root domain - its existing mail would be affected.

Then:

1. Register it: `client.add_domain("agents.acme.com")` (REST: POST {base}/v1/domains
   with `{{"domain": "agents.acme.com"}}`).
2. Show the developer the returned `dns_records` and tell them to add each at
   their DNS provider. A zone file for bulk import is at
   `{base}/v1/domains/{{id}}/zone-file`.
3. Poll `GET {base}/v1/domains/{{id}}` until `status` is `active` (DNS can take
   minutes). Do not guess; wait for the developer to confirm the records are in.
4. Connect on the domain: `client.connect_email(domain="agents.acme.com")` -
   the address is now on their domain, sending is DKIM-signed as their domain.
   To pick the exact address, add `username="kernel"` ->
   kernel@agents.acme.com (custom domains only; 409 if taken).

## Adding Slack (ask which kind of app)

Slack comes THREE ways - STOP and ask the developer:

> Slack connects as an app installed to a workspace. Three options:
>   a) Quick (one click) - add our shared app; nothing to create. Custom name +
>      icon, so it still looks like your brand.
>   b) Branded - your OWN Slack app in YOUR workspace (~5 min); you control the
>      app name and the @mention.
>   c) Distribute - your OWN Slack app that you PUBLISH and install into your
>      CUSTOMERS' workspaces (the same way Caspian does its shared app). ~10 min
>      once, then one "Add to Slack" link per customer.
> Which - Quick, Branded, or Distribute?

### a) Quick - shared app (one-click OAuth, no app to create)

Ask the developer what name (and optional icon URL) the agent should post under:

> What should the agent be called in Slack? (e.g. "Acme Support") Optionally an
> icon image URL too.

Then: `result = client.install_slack(display_name="<their name>", icon_url="<url>")`
returns a connection with an `authorize_url` ("Add to Slack"). Give the developer
that URL: "Open this, pick your workspace, and authorize." The shared app installs
into their workspace, the connection flips to `active` on its own (poll
`GET {base}/v1/connections/{{id}}` or watch for `connection.active`), and the agent
posts under their name + icon. Messages in that workspace reach the same
`on_message` handler. If `install_slack()` 400s, the shared app isn't configured on
this gateway - use (b). NEVER default the name to "Caspian"/an infra name - ask.

### b) / c) Your own Slack app (b = your workspace, c = distribute to customers)

Both use an app the developer OWNS; (c) just also turns on public distribution so
they can install it into OTHER workspaces. Walk the developer through creating it -
tell them to do EXACTLY this at api.slack.com/apps -> Create New App -> From scratch:

1. OAuth & Permissions -> Redirect URLs -> add `{base}/v1/oauth/slack/callback`, Save.
2. OAuth & Permissions -> Bot Token Scopes -> add: chat:write, chat:write.customize,
   channels:history, im:history, app_mentions:read.
3. Event Subscriptions -> toggle On -> Request URL:
   `{base}/internal/providers/slack/webhooks` (it verifies instantly). Under
   "Subscribe to bot events" add: message.channels, message.im, app_mention, Save.
4. (Option c ONLY) Manage Distribution -> finish the checklist ->
   "Activate Public Distribution". This is what lets the app install into your
   customers' workspaces, not just your own.
5. Basic Information -> App Credentials -> copy Client ID, Client Secret, and
   Signing Secret, and paste all three to me.

Then connect:
- b) your workspace: `client.connect_slack(slack_client_id=..., slack_client_secret=...,
  slack_signing_secret=...)` returns a connection with an `authorize_url`. Open it,
  pick your workspace, Allow -> the connection goes `active`.
- c) per customer: call `connect_slack(...)` with the SAME app creds but a DIFFERENT
  `customer_id` for each customer (ask the developer for a label per customer, e.g.
  their company name). Each call returns its OWN `authorize_url` ("Add to Slack").
  Send that link to that customer; they pick THEIR workspace and Allow. Every
  customer's workspace routes back to the same `on_message` handler, isolated by
  customer_id, and inbound from all of them shares the one events URL - routed
  automatically by app + team.

CLI: `caspian connect slack` prints the authorize link.

## Adding Instagram or Facebook (OAuth channels)

Same OAuth-link pattern (gateway-configured app):

1. `client.connect_instagram(...)` / `connect_facebook(...)` returns a connection
   with an `authorize_url`.
2. Give the developer that URL: "Click this to authorize in your account."
3. They approve; the platform redirects to our callback and the connection flips
   to active. Poll `GET {base}/v1/connections/{{id}}` until `active`.
4. The same `on_message` handler now answers on that channel.

## Adding Discord (ask which kind of bot)

The agent connects to Discord as a bot (two-way: people message it, it replies).
There are two ways to get that bot - STOP and ask the developer:

> Discord connects as a bot. Two options:
>   a) Quick (one click) - add our shared bot to your server; no token to create.
>      You give it a custom name; it uses a shared avatar.
>   b) Branded - create your OWN bot (~5 min) so it has your own name AND avatar.
> Which - Quick (shared) or Branded (your own)?

### a) Quick - shared bot (one-click OAuth, no token)

First ASK the developer what name the bot should show in their server:

> What should the bot be called in your server? (e.g. "Acme Support")

Then pass it as `display_name`:
`result = client.install_discord(display_name="<their chosen name>")` returns a
connection with an `authorize_url`. Give the developer that URL: "Open this, pick
your server, and authorize." Discord adds the shared bot, renames it to their
chosen name in that server, and the connection flips to `active` on its own (poll
`GET /v1/connections/{{id}}` or watch for `connection.active`). Messages in that
server then reach the same `on_message` handler; `message.reply()` answers. If
`install_discord()` 400s, the shared bot isn't configured on this gateway - use
(b). A 409 on the callback means that server is already linked to an agent. (The
custom name is a per-server nickname; avatar stays shared - use (b) for a custom
avatar too.) NEVER default the name to "Caspian" or an infra name - ask.

### b) Branded - bring your own bot

> Create a bot at discord.com/developers -> New Application -> Bot -> Reset
> Token -> copy it. Enable the "Message Content Intent" under Bot settings.
> Then paste me the token.

Then `client.connect_discord(bot_token="<token>")`. Same `on_message` handler
answers; `message.reply()` replies in Discord. A 409 means that bot is already
connected elsewhere - use a different one.

Either way, replies use the developer's agent - only the bot's name/avatar differs
(shared name vs their own bot). Never brand the bot as Caspian/the gateway.

## Adding Telegram (optional second channel)

Telegram needs one thing only a human can create: a bot token. STOP and ask
the developer for it with exactly these instructions:

> Open Telegram and message @BotFather. Send /newbot, choose a name and a
> username, and paste me the token it gives you (looks like 7123456789:AAE...).

Do not proceed without the token and never invent one. Once the developer
provides it:

```python
telegram = client.connect_telegram(bot_token="<token from developer>")
print("Telegram bot:", telegram["address"])   # @their_bot
```

Or via CLI: `caspian connect telegram --bot-token <token>`.

The same @client.on_message handler receives Telegram messages; message.reply()
answers in the chat. No other setup - the gateway registers the webhook.

To verify: tell the developer to open Telegram, search for the bot's @username,
and send it any message. The running integration must print it and reply within
a few seconds.

Error handling: a 409 from connect means that bot token is already connected
elsewhere - ask the developer for a different bot. A 422 naming `bot_token`
means it was missing or malformed.

## Adding WhatsApp (ask which provider first)

WhatsApp is served by TWO providers on this gateway, so it appears twice in
/v1/channels. They behave differently, so STOP and ask the developer which one
with exactly this choice:

> WhatsApp has two options here:
>   1. Twilio - fastest to test. A shared sandbox number works in minutes (great
>      for a demo); a dedicated production number needs a paid Twilio account +
>      WhatsApp sender approval.
>   2. Meta (direct) - connect your OWN branded WhatsApp Business number through
>      a one-time Meta popup (Embedded Signup). Best for production and brand.
> Which do you want - Twilio (quick test) or Meta (branded)?

Pass their choice as `provider=` on connect. Same `on_message` handler either way.

### Option 1 - Twilio (test now)

```python
wa = client.connect_whatsapp(provider="twilio-whatsapp")
print("WhatsApp on:", wa["address"])   # the Twilio number (sandbox by default)
```

For inbound to reach the agent, the developer must do two one-time things in the
Twilio Console (Messaging -> Try it out -> Send a WhatsApp message):

1. Set "When a message comes in" to
   `{base}/internal/providers/twilio-whatsapp/webhooks` (HTTP POST), and Save.
2. From their own WhatsApp, send the shown `join <two-words>` to the sandbox
   number (+1 415 523 8886) to opt in.

Then any message they send that number reaches the same handler and gets a reply.

### Option 2 - Meta (branded, Embedded Signup)

A one-time Meta popup connects the developer's (or their customer's) own WhatsApp
Business number - no tokens to copy or paste:

```python
session = client.start_whatsapp_onboarding()
print("Open this to connect WhatsApp:", session["launcher_url"])
```

Give the developer that `launcher_url`. They click through the Meta popup once
and their number provisions onto the agent. Poll `GET {base}/v1/connections/{{id}}`
until status is `active` (or watch for the `connection.active` event). If
`start_whatsapp_onboarding()` returns 400, Embedded Signup isn't configured on
this gateway yet - fall back to Option 1 (Twilio).

Either way, WhatsApp business rules apply: the agent can reply freely for 24h
after a user messages it; starting a conversation cold needs an approved
template. The developer's `on_message` code doesn't change - the gateway handles
the difference.

## Adding SMS / phone (bring your own number - ask which provider)

The agent gets a real phone line for two-way SMS. It's BRING-YOUR-OWN: the
developer uses their OWN Twilio or Telnyx account + a number they own there, so
their account handles billing and A2P/10DLC registration - Caspian just relays.
STOP and ask which provider:

> SMS runs through your own CPaaS account. Which do you use - Twilio or Telnyx?
> You'll need your account credentials and a phone number you own there.

### Twilio
Ask for: Account SID + Auth Token (console.twilio.com) and your Twilio number (E.164).

```python
sms = client.connect_phone(provider="twilio",
    account_sid="AC...", auth_token="...", from_number="+1...")
```

Then tell the developer to set their Twilio number's inbound "A message comes in"
webhook (Messaging config) to:
`{base}/internal/providers/twilio/webhooks/<your +E.164 number>`

### Telnyx
Ask for: API Key (portal.telnyx.com) and your Telnyx number (E.164).

```python
sms = client.connect_phone(provider="telnyx", api_key="KEY...", from_number="+1...")
```

Then set that number's inbound webhook to:
`{base}/internal/providers/telnyx/webhooks/<your +E.164 number>`

The same `on_message` handler answers SMS; `message.reply()` texts back; it can
also `initiate` (cold-start) a text. A 409 means that number is already connected.
Note: US outbound SMS needs A2P 10DLC (or a verified toll-free) on THEIR Twilio/
Telnyx account - that's on the developer's CPaaS account, not this gateway.

## Adding iMessage (no setup - like email)

iMessage needs NOTHING from the developer - the gateway owns the number over
Caspian's managed iMessage line (Apple has no public API). Just connect:

```python
imessage = client.connect_imessage()
print("iMessage number:", imessage["address"])
```

The same `on_message` handler answers over iMessage. It also supports
cold-initiate (message a number that never messaged first), unlike WhatsApp.

To verify: text the printed number from an iPhone - the running integration
prints the inbound message and replies within a few seconds.

Note: this gateway shares one iMessage number, so it's one connection at a time
(fine for a demo; per-agent numbers are a follow-up). A 409 / "already connected"
on connect means another agent currently holds it.

## Adding X / Twitter (reactive DM bot - ask which kind)

The agent runs an X account as a bot: people DM the account, the agent replies.
REACTIVE only - it answers DMs it receives, never cold-DMs (X ToS). The account
should be labelled "Automated" in X settings. Two ways - STOP and ask:

> X connects your account as a DM bot. Two options:
>   a) Quick (one click) - authorize with "Sign in with X"; nothing to paste.
>   b) Branded - paste your own X app's tokens (create one at developer.x.com).

### a) Quick - one-click OAuth (no tokens to paste)

```python
result = client.install_x()
print("Authorize here:", result["authorize_url"])
```

Give the developer the `authorize_url`, or open it. It says "Sign in with X ->
authorize." IMPORTANT - tell the developer clearly:

> Whatever X account you are LOGGED INTO when you click "Authorize" becomes the
> bot - that handle is what your users will DM. If you don't want your personal
> account to be the bot, create a dedicated X account first (e.g. @yourbrandbot),
> log into it, THEN open this link.

Once they approve, that account becomes the bot and goes active. Then just run the
same `on_message` handler + `client.listen()`. If `install_x()` 400s, the shared X
app isn't configured on this gateway - use option (b).

### b) Branded - bring your own account tokens

Ask the developer for their app's Access Token, Access Token Secret, and numeric
user id (from developer.x.com, Keys & Tokens tab; App permissions "Read and write
and Direct message"). The user id is the part before the dash in the access token.

```python
x = client.connect_x(
    access_token="<Access Token>",
    access_secret="<Access Token Secret>",
    user_id="<numeric user id>",
)
```

Either way: the same `on_message` handler answers X DMs and `message.reply()`
replies in the same DM thread. Inbound arrives by polling (a few seconds' latency,
not instant) - no webhook to set up. A 409 means that X account is already
connected to another agent.

To verify: DM the connected account from any other X account - the running
integration prints the inbound DM and replies within ~10 seconds. Note: X DM
reads/sends are pay-per-use, so the account needs a small credit balance.

## Notes

- `connect_email()` is idempotent: calling it again (e.g. on agent restart)
  returns the same connection and address instead of provisioning a new inbox.
- `connect_email()` with no arguments uses a default customer/agent scope.
  For multi-tenant products pass explicit `customer_id` and `agent_id`
  (create them via `client.create_customer(name)` / `client.create_agent(name)`).
- `client.listen()` blocks forever. For custom loops poll
  `client.events(after_seq=cursor)`; events are strictly ordered by `seq`.
- Do not reply to the same message twice; `listen()` already dedupes within
  a run. Persist your own cursor if you need replies across restarts.
- A "typing…" indicator is shown automatically while your handler runs, on
  channels that support it (Discord, Telegram). No code needed; for long work
  call `message.typing()` again to keep it alive (it lasts ~5-10s per call).
- For channels WITHOUT a typing indicator (X, SMS, email), pass
  `client.listen(ack="On it, one moment…")` to send an instant acknowledgement
  reply the moment a message arrives, so the human knows the agent is working
  while it thinks; the real answer follows from the handler. (caspian-sdk >= 0.1.2;
  JS: `client.listen({{ ack: "On it…" }})`.)
- REST reference: {base}/docs
"""


@router.get("/SKILL.md", response_class=PlainTextResponse)
@router.get("/llms.txt", response_class=PlainTextResponse)
def skill(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto")
    if forwarded_proto == "https" and base.startswith("http://"):
        base = "https://" + base.removeprefix("http://")
    settings = request.app.state.settings
    dashboard = settings.billing_dashboard_url
    email_domain = settings.ses_domain or "your-agent-domain.com"
    return SKILL_TEMPLATE.format(
        base=base, repo=REPO, dashboard=dashboard, email_domain=email_domain
    )
