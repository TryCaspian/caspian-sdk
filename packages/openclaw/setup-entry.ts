// Setup wizard entry: env-var based; the gateway does the heavy lifting.
import { defineSetupPluginEntry } from "openclaw/plugin-sdk/channel-core";

export default defineSetupPluginEntry({
  id: "caspian",
  env: ["COMM_API_KEY", "COMM_BASE_URL"],
  instructions:
    "Set COMM_API_KEY (mint one: POST {base}/v1/projects/sandbox) and COMM_BASE_URL. Full agent-readable guide: GET {base}/SKILL.md",
});
