import { defineConfig } from "tsup";

// OpenCode loads plugins with Bun. `caspian-sdk` and `@opencode-ai/plugin`
// stay external so the host / npm install resolves them.
export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm"],
  dts: true,
  clean: true,
  sourcemap: true,
  target: "esnext",
  outDir: "dist",
  external: ["caspian-sdk", "@opencode-ai/plugin"],
});
