---
description: Connect Caspian email (sandbox key or dashboard paste) and enable email in caspian.json
---

Load the **caspian-connect-email** skill, then:

1. Call tool `caspian_setup_credentials` with `mode=status`
2. If not configured, ask the user:
   - **sandbox** — free instant key (no signup): `caspian_setup_credentials` `mode=sandbox`
   - **paste** — sign in at https://dashboard.trycaspianai.com/login , copy a project API key, then `caspian_setup_credentials` `mode=paste` with `apiKey`
3. If RESTART REQUIRED after setup, tell them to quit and relaunch OpenCode, then rerun this command
4. Call `caspian_connections`
5. Call `caspian_connect_email`
6. If RESTART REQUIRED again (channels), quit and relaunch so admit includes email

Arguments:
$ARGUMENTS
