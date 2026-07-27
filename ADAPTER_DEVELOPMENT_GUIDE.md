# Adapter Development Guide

## Overview

This guide summarizes the common patterns used when implementing a new channel adapter in Caspian. It complements the contribution guide by collecting implementation conventions in one place.

## Implement the ChannelProvider interface

Every adapter should implement the ChannelProvider interface and provide the required methods such as:

- provision
- send
- reply
- parse_webhook

Optional methods (for example typing indicators or OAuth hooks) should only be implemented when the platform supports them.

## Declare capabilities

Expose the adapter's supported capabilities through the capabilities set so the framework can determine which features are available.

## Verify inbound requests

If the platform signs webhook requests, verify the signature before processing the payload. Reject invalid requests immediately.

## Normalize inbound payloads

Convert incoming platform payloads into the common InboundMessage format so the rest of the framework can work independently of the messaging platform.

## Fake provider

Provide an in-memory fake implementation that accepts the platform's real inbound payload format. This allows offline testing without external services.

## Registry and configuration

After creating a new adapter:

- Register it in the provider registry.
- Add any required configuration values.
- Keep configuration consistent with existing providers.

## Testing

New adapters should include tests for:

- Payload normalization
- Webhook signature verification (valid and invalid)
- Routing behavior
- Provider-specific edge cases

## Reference adapters

The following adapters provide useful implementation examples:

- Telegram
- Slack
- X
- Modem

Review these implementations for repository conventions before creating a new adapter.
