---
name: caspian-toast-test
description: >-
  Dummy skill to test OpenCode TUI toast UI (and optional system notification)
  via caspian_test_toast. Use when the user says test toast, /caspian:toast,
  or wants to verify Caspian notifications.
license: Apache-2.0
compatibility: opencode
metadata:
  purpose: dummy-toast-ui-test
---

# Caspian toast test (dummy)

Verify OpenCode toast UI without sending email.

## When to use

- User asks to “test toast”, “show a toast”, or “test Caspian notification”
- User runs `/caspian:toast` or `/caspian-toast`

## How

1. Call tool **`caspian_test_toast`** immediately.
2. Optional args:
   - `title` — default `Caspian toast test`
   - `message` — short body
   - `variant` — `info` | `success` | `warning` | `error`
   - `system` — `true` (default) also fires a desktop notification
3. Report the tool output (`shown` / `FAILED`). Do not invent a toast.

## Example

```
caspian_test_toast({
  title: "Caspian toast test",
  message: "If you see this banner, toast UI works.",
  variant: "success",
  system: true
})
```
