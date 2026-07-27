---
description: Send or reply via Caspian email (ask thread vs new)
---

You are handling a Caspian email action.

**First decide: reply on thread vs new email.**

1. If this session shows `[caspian:email]` / inbound mail context, or the user is answering that mail:
   - **Ask** (unless they already said clearly):  
     “Reply on this email thread, or send a separate new email?”
   - Thread reply → call **`caspian_reply_email`** with `body` only.
   - New email → call **`caspian_send_email`** with `to`, real `subject`, `body`, and `confirmNewEmail=true`.

2. If there is no inbound thread and they want to mail someone new:
   - Call **`caspian_send_email`** with `to`, a real `subject` (never bare `Re:` / empty), and `body`.

Rules:
- Never use subject `Re:` alone or omit subject on a new email.
- Do not call `caspian_send_email` to answer an existing thread.
- Do not pretend mail was sent without a successful tool result.

Arguments from the user:
$ARGUMENTS
