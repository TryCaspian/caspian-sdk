import eslint from "@eslint/js"
import tseslint from "typescript-eslint"

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    ignores: ["dist/**", "node_modules/**"],
  },
  {
    files: ["src/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "caspian/telegram",
              message: "CLI must not import adapters.",
            },
            {
              name: "caspian/discord",
              message: "CLI must not import adapters.",
            },
            {
              name: "caspian/slack",
              message: "CLI must not import adapters.",
            },
          ],
          patterns: [
            {
              group: ["caspian/telegram", "caspian/discord", "caspian/slack", "caspian/voice", "caspian/email", "caspian/sms", "caspian/whatsapp", "caspian/messenger", "caspian/imessage", "caspian/x", "caspian/linear", "**/adapters/**"],
              message: "CLI must not import adapters. Catalog + call dispatch on command_tag.",
            },
          ],
        },
      ],
    },
  },
)
