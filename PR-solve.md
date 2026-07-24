# PR: Normalize reaction and command events across adapters and SDKs

## Problem

Caspian's event model was effectively a `message.received → reply` loop. The
adapter layer (`parse_webhook`) only returned `InboundMessage`, silently dropping
platform-native events like emoji reactions and slash commands. The SDK layer had
partial scaffolding for reactions (`on_reaction` existed but was never fed by
adapters) and nothing for commands at all.

## What this PR does

Extends the event model end-to-end — from raw platform payloads through to
user-registered handlers — for **reactions** (add/remove) and **slash/bot
commands**, with a capability-gated, channel-agnostic design.

### Adapter layer (`packages/adapters/`)

| File | Change |
|---|---|
| `base.py` | Added `InboundReaction`, `InboundCommand` frozen dataclasses; `InboundEvent` union type alias; `Capability.REACTIONS` and `Capability.COMMANDS`; updated `ChannelProvider.parse_webhook` return type from `list[InboundMessage]` to `list[InboundEvent]` |
| `slack.py` | `parse_event()` now handles `reaction_added`/`reaction_removed` → `InboundReaction`; new `parse_slash_command()` normalizes slash command payloads → `InboundCommand`; new `react()` method (calls `reactions.add`); added `REACTIONS`/`COMMANDS` capabilities; expanded OAuth scopes |
| `discord.py` | `parse_gateway_message()` now handles `MESSAGE_REACTION_ADD/REMOVE` → `InboundReaction` and `INTERACTION_CREATE` type 2 → `InboundCommand`; new `react()` method (PUT reactions endpoint); added `REACTIONS`/`COMMANDS` capabilities |
| `telegram.py` | `parse_update()` now handles `message_reaction` updates → `InboundReaction` and bot command entities at offset 0 → `InboundCommand`; new `react()` method via `setMessageReaction`; `allowed_updates` expanded to include `message_reaction`; added `REACTIONS` capability |
| `fake_social.py` | Added `reaction_payload()` and `command_payload()` fixture helpers to `FakeSlackProvider` and `FakeDiscordProvider`; added `react()` stubs; updated `parse_webhook` return types |
| All other adapters | Updated `parse_webhook` return type to `list[InboundEvent]` for Protocol conformance |

### Python SDK (`sdks/python/`)

| File | Change |
|---|---|
| `client.py` | Added `Command` dataclass (with `reply()` method); added `_command_handlers` list; added `on_command()` decorator; added `_dispatch_command()` method; extended `_dispatch_event()` to route `command.received` events |
| `__init__.py` | Exports `Command` |

### TypeScript SDK (`sdks/typescript/`)

| File | Change |
|---|---|
| `client.ts` | Added `Command` class (with `reply()` method); added `CommandHandler` type; added `onCommand()` method; added `dispatchCommand()` method; extended `dispatchEvent()` to route `command.received` events |
| `index.ts` | Exports `Command` and `CommandHandler` |

### Tests

| File | New tests |
|---|---|
| `test_slack.py` | 8 tests: reaction added/removed normalization, reaction signature verification, slash command normalization (with/without args, empty), slash command signature verification |
| `test_discord.py` | 10 tests: reaction add/remove, shared-bot routing for reactions, empty emoji, slash command with/without options, shared-bot DM routing, non-command interaction ignored |
| `test_telegram.py` | 6 tests: reaction added/removed, multi-emoji reaction diff, bot command with/without args, non-command message not misclassified |
| `test_sdk.py` | 3 tests: command dispatch end-to-end, command reply routing, unknown event types silently ignored |

## Design decisions

1. **Union return type** (`InboundEvent = InboundMessage | InboundReaction | InboundCommand`)
   instead of a separate method — single entry point, all providers update their
   type hints consistently.

2. **`react()` as an optional method** on adapters (not on the `ChannelProvider`
   Protocol) — the gateway already capability-gates the outbound react API call;
   only adapters that physically support reactions implement it.

3. **Capability-gated** — `Capability.REACTIONS` and `Capability.COMMANDS` are
   declared by adapters that support them; email/SMS adapters don't pretend to
   have reactions.

4. **Telegram command detection** uses the `entities` array (bot_command at
   offset 0) rather than relying on a leading `/` — this matches Telegram's own
   parsing and avoids false positives on messages that start with `/` but aren't
   commands.

## Verification

- **119 Python tests pass** (17 new)
- **28 TypeScript tests pass**
- **ruff lint clean**
