import eslint from "@eslint/js"
import tseslint from "typescript-eslint"

export default tseslint.config(
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    ignores: ["dist/**", "node_modules/**", ".dependency-cruiser.cjs"],
  },
  {
    files: ["src/core/**/*.ts"],
    rules: {
      "no-restricted-globals": [
        "error",
        {
          name: "Date",
          message: "Time in core comes from Effect Clock, not Date.",
        },
        {
          name: "setTimeout",
          message: "Timers are banned in core.",
        },
        {
          name: "setInterval",
          message: "Timers are banned in core.",
        },
        {
          name: "fetch",
          message: "HTTP is banned in core.",
        },
      ],
      "no-restricted-properties": [
        "error",
        {
          object: "Math",
          property: "random",
          message: "Entropy in core comes from Effect Random.",
        },
        {
          object: "Date",
          property: "now",
          message: "Time in core comes from Effect Clock.",
        },
      ],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "node:http",
              message: "node:http is banned in core.",
            },
            {
              name: "node:https",
              message: "node:https is banned in core.",
            },
            {
              name: "node:fs",
              message: "node:fs is banned in core.",
            },
            {
              name: "node:crypto",
              message: "node:crypto is banned in core.",
            },
            {
              name: "undici",
              message: "HTTP clients are banned in core.",
            },
            {
              name: "axios",
              message: "HTTP clients are banned in core.",
            },
          ],
          patterns: [
            {
              group: ["node:*"],
              message: "Node builtins are banned in core.",
            },
            {
              group: ["../adapters", "../adapters/*", "**/adapters/**"],
              message: "core must not import adapters.",
            },
            {
              group: ["../provision", "../provision/*", "**/provision/**"],
              message: "core must not import provision.",
            },
          ],
        },
      ],
    },
  },
)
