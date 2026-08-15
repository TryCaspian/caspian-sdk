# caspian (TypeScript)

Rewrite of the Caspian TypeScript SDK. Bot developers will write a Chat
SDK-shaped API that desugars into a small Effect kernel. This package is not
the legacy `CommClient`.

Phase 0: workspace, strict TypeScript, bun tests, and CI gates that keep
`src/core` free of I/O and channel code.

```bash
bun install
bun run ci
```
