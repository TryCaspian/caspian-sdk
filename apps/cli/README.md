# caspian-cli

The **Caspian CLI** — provision a project, connect channels, sign in, manage
billing, and tail events from your terminal. Ships the `caspian` command.

```bash
pip install caspian-cli        # or: pipx install caspian-cli  /  uvx caspian-cli
caspian init                   # mint a sandbox key, write .env
caspian connect email          # free, instant (also: telegram, slack, discord)
caspian login                  # one-time sign-in for paid channels (x, whatsapp, imessage)
caspian billing                # balance, spend, limits
caspian topup 5                # add credit via Stripe checkout
caspian listen                 # tail inbound/outbound events
```

The CLI talks to the hosted gateway at `https://api.trycaspianai.com` by default
(set `CASPIAN_BASE_URL` for a self-hosted gateway). Pairs with the
[`caspian-sdk`](https://pypi.org/project/caspian-sdk/) library.

> `caspian` is the command; `comm` is a legacy alias. Reads
> `CASPIAN_API_KEY` / `CASPIAN_BASE_URL` (falls back to the legacy `COMM_*`).
