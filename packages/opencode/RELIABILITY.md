# Reliability model — caspian-opencode-plugin

Maps the org reliability tenets onto this plugin. **Start here when changing anything.**

## Critical Path (CP)

The CP is narrow on purpose. If CP holds, a whole class of failures disappears.

| CP component | Role | Why it is CP |
|---|---|---|
| **Inbound switch** | Caspian `message.received` → admit → capacity gate → OpenCode `session.prompt` | Silent drop = user thinks agent is dead |
| **Outbound reply switch** | Agent text → Caspian `reply` (fail-fast + bounded retry) | User never sees the answer |
| **Listen heartbeat** | Detect stuck / dead poll loop | No inbound without a live listener |
| **Session map** | `conversationId` ↔ OpenCode `sessionId` | Wrong map = crossed threads / blast radius |
| **Per-conversation limiter** | Concurrency + rate limits | Capacity exhaustion / noisy neighbor |
| **Feature switchboard** | Critical vs non-critical kill switches | Degrade without killing CP |
| **CP circuit breaker** | Fail-fast on Caspian/OpenCode reply path | Prefer failover/degrade over hang |

Everything else (typing, toasts, connect/OAuth, proactive send, subject cosmetics) is **non-CP** and must be safe to ignore on error (`nonCritical`).

### NonCP ↛ CP (hard rule)

Proactive send (`caspian_send_*`) uses a **separate** `caspianOutboundCircuit`.  
Inbound auto-reply / thread reply uses `caspianCpCircuit`.  
N failed Discord/Telegram/email *sends* must **never** open the breaker that protects listen→reply.

Connect / `ensureEmail` must **never** block listen start (background + timeout).

## Abundance / Decentralization (AD)

| AD control | Implementation |
|---|---|
| Redundancy / failover | Separate CP vs outbound circuits; onboard CLI→HTTP mint failover; fail-fast + backoff; degraded reply under capacity |
| Isolation / blast radius | Admit `channels` allowlist; email-only identity filters; one in-flight per conversation; global concurrency |
| Capacity abundance | Rate limits + reject (fail-fast), not unbounded queues; cheap CP before prompt |
| Stagger / rollback | Feature switches; edit `channels` + restart; kill switch `switches.enabled` |
| Error unification | In-process metrics counters for SLO signals |

## Fault / Capacity / Change

| Cause | Remedy in this plugin |
|---|---|
| **Fault** | Circuit breakers; heartbeat; fail-fast; degraded “agent busy / error” reply; admit walls |
| **Capacity** | Per-conversation + global limits; degraded reply when saturated |
| **Change** | Unit + fault/capacity tests; typecheck; NonCP cannot open CP circuit; CP changes need review |

## SLOs (v0 targets — measure from day one)

| SLO | Target | Signal |
|---|---|---|
| Inbound→prompt attempt success | ≥ 99.9% when under capacity | `inbound.prompt_ok` / `inbound.prompt_fail` |
| Outbound reply success (after agent) | ≥ 99.5% | `outbound.reply_ok` / `outbound.reply_fail` |
| Listen liveness | Heartbeat age < 30s while running | `listen.heartbeat_stale` |
| Handler p99 under load | < 60s excluding model time | `inbound.handler_ms` |

## Onboarding failover (anyone-can-use)

Credential acquisition is **non-CP** to mint failures (plugin goes idle + toast), with fast cold start:

1. Existing env / `.env` / `~/.config/opencode/caspian.env`
2. If `caspian` is on PATH → `caspian init` (bounded timeout)
3. Else **HTTP mint** `POST /v1/projects/sandbox` (no CLI / no prior `.env`)
4. Persist to project `.env` + `~/.config/opencode/caspian.env`
5. Listen starts immediately; `connectEmail` runs in background (timeout)

`uvx`/`pipx` bootstrap is **off by default** so a first-time package download cannot stall OpenCode. Opt in with `tryUvxBootstrap` in code if needed.

Disable with `switches.autoOnboard: false`.

## Channel integrations (admit as blast-radius wall)

| Channel | Connect (non-CP) | Inbound CP | Outbound (own circuit) |
|---|---|---|---|
| **email** | auto / `caspian_connect_email` | yes when admitted | `caspian_send_email` / reply |
| **telegram** | bot token → enable channel + **restart** | yes when admitted | `caspian_send_telegram` |
| **discord** | install OAuth or `DISCORD_BOT_TOKEN` → enable + **restart** | yes when admitted | `caspian_send_discord` |

**Rules**

1. **Admit is CP for blast radius** — unknown / disabled channels never enter prompt/reply.
2. **Connect / OAuth / token setup is non-CP** — must not abort or delay `listen`.
3. **Secrets stay out of `caspian.json`**.
4. **Restart after enabling a channel** — admit loaded at plugin start.
5. **Outbound uses its own circuit** — fail-fast + metrics; never shares CP reply budget.
6. **Identity filters are email-only**.

## Tenet scorecard (plugin scope)

| # | Tenet | Status | Notes |
|---|---|---|---|
| 1 | Redundancy / failover / degrade | **Partial** | Circuits + degrade + onboard failover; single-process listen (no multi-AA) |
| 2 | CP 100× simple, big limits | **Pass** | Narrow pipeline; concurrency/rate caps |
| 3 | Blast radius / boundaries | **Pass** | Admit channels + per-conv isolation |
| 4 | Anomaly / early detection | **Partial** | Heartbeat + counters; no auto-RCA export |
| 5 | Dynamic capacity | **Partial** | Static limits + fail-fast (enough for single agent) |
| 6 | Stagger / A/B / rollback | **Partial** | Switches + channel restart; no % A/B |
| 7 | Load / failure / chaos tests | **Partial** | Unit fault + capacity suites; no prod chaos |
| 8 | RCA / CoE process | **N/A (org)** | Documented model; process lives outside this repo |
| 9 | Ambitious SLO + measure | **Partial** | SLO table + in-process metrics |
| 10 | Collective ownership / IR culture | **N/A (org)** | Reinforced by CP/NonCP rules in code review |

## CP patterns present

| Pattern | Present? |
|---|---|
| Failover switch (circuit) | Yes (CP + outbound separate) |
| Rate limiting / isolation | Yes |
| Critical–NonCritical divider | Yes (`nonCritical`, switches) |
| On errors ignore non-critical | Yes |
| Health check / heartbeat | Yes (metrics on stale) |
| Capacity gate before prompt | Yes |
| Stagger via config flags | Yes |
| Load balancer / autoscaler | No (single OpenCode process — intentional) |
| Prod chaos harness | No (unit chaos only) |

## Non-goals (still)

- Multi-region active-active of the plugin process
- Media / attachments
- Slack / paid channels (WhatsApp, X, phone, …)
- Metrics export / burn-rate alerting / auto-RCA
