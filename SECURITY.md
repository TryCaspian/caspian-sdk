# Security Policy

## Reporting a vulnerability

Please email **rushant@saasden.club** with the details — proof of concept, affected module, and impact. Do not open a public issue for security problems.

We'll acknowledge within 72 hours and keep you updated as we fix it.

## Scope notes

- Webhook signature verification (Slack, GitHub, Meta, Telegram secret header, X CRC, SES/SNS) is a security boundary in every adapter — bypasses are always in scope.
- Credential handling: adapters receive per-connection credentials from their caller and must never log or persist them.
