# caspian (TypeScript CLI)

Thin bun + Effect client of the rewrite Chat SDK. Catalog discovers. `call`
invokes. Channels and threads are resources. This is not the CommClient-era
`connect` / `listen` / `billing` CLI.

```bash
# hosted needs a key: --api-key, CASPIAN_API_KEY, or sign up
# caspian init   # optional: mint a sandbox key into .env

caspian channels add telegram
caspian channels add discord --name Maya
caspian channels add telegram --via self-host --bot-token "$TG" \
  --webhook-url https://myapp.example.com/hook
caspian channels ls

caspian catalog
caspian catalog search "send a photo"
caspian catalog get telegram.send-photo

caspian call post --thread telegram:123:456 --text "shipping now"
caspian call post --thread slack:C123:ts --text "shipped"
caspian call telegram.send-photo --thread telegram:123:456 --file ./graph.png

caspian threads ls --channel telegram
caspian threads tail telegram:123:456
```

| Job | The one command | Not also |
|---|---|---|
| Key | `--api-key` / `CASPIAN_API_KEY` / signup | `caspian init` is optional sandbox |
| Identity | `caspian channels add` / `ls` | `connect`, `status`, `watch` |
| Discover | `caspian catalog` / `search` / `get` | invoking from catalog |
| Do | **`caspian call <id>`** | `slack post`, `telegram send-photo` argv, `threads reply` |
| List chats | `caspian threads ls` | — |
| Follow events | `caspian threads tail` | `channels watch`, `listen` |

Omit `--via` means hosted. Thread ids are `telegram:…` / `slack:…`, never a
platform chat id. Hosted jobs need a key: `--api-key` / `CASPIAN_API_KEY`,
optional `--gateway` / `CASPIAN_BASE_URL`, or sign up at
https://dashboard.trycaspianai.com. Catalog and self-host
`channels add` do not. `caspian init` is optional (sandbox key into `.env`).

Argv desugars to `Intent` (syntax). `planIntent` is the denotation: a `Plan`
(gateway request, local catalog value, or init). `runPlan` is one interpreter;
`fakeGatewayClient` / `chaosGatewayClient` are the others. Failure is
`UsageError` data.

```bash
cd packages/cli
bun install
bun run ci
```
