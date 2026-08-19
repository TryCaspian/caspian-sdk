# caspian (TypeScript CLI)

Thin bun + Effect client of the rewrite Chat SDK. Catalog discovers. `call`
invokes. Channels and threads are resources. This is not the CommClient-era
`connect` / `listen` / `billing` CLI.

The CLI secret lives in **`~/.caspian/.env`** (override the directory with
`CASPIAN_HOME`). That is not this repo's `.env`. Sign-in is device-auth in the
browser — there is no sandbox mint.

```bash
caspian init                 # asks: cli, project, or agent
caspian init cli             # CLI secret → ~/.caspian/.env
caspian init project         # also write ./.env for the SDK
caspian init agent           # CLI secret + ./.env + .caspian/AGENT.md
caspian login                # sign in only; writes the CLI secret

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
| Set up / key | `caspian init` / `caspian login` | sandbox mint into the repo `.env` |
| Identity | `caspian channels add` / `ls` | `connect`, `status`, `watch` |
| Discover | `caspian catalog` / `search` / `get` | invoking from catalog |
| Do | **`caspian call <id>`** | `slack post`, `telegram send-photo` argv, `threads reply` |
| List chats | `caspian threads ls` | — |
| Follow events | `caspian threads tail` | `channels watch`, `listen` |

Omit `--via` means hosted. Thread ids are `telegram:…` / `slack:…`, never a
platform chat id. Hosted jobs need a key: `caspian init` / `caspian login`,
`--api-key` / `CASPIAN_API_KEY`, optional `--gateway` / `CASPIAN_BASE_URL`.
Catalog and self-host `channels add` do not.

Argv desugars to `Intent` (syntax). `planIntent` is the denotation: a `Plan`
(gateway request, local catalog value, login, or init). `runPlan` is one
interpreter; `fakeGatewayClient` / `chaosGatewayClient` are the others.
Failure is `UsageError` data.

```bash
cd packages/cli
bun install
bun run ci
```
