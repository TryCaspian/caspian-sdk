import { defineConfig } from "tsup";

// Two entry points: the full channel entry and the lightweight setup entry.
// `openclaw` is a peer dependency (host-provided) and stays external; `caspian-sdk`
// is a real runtime dependency and is also left external so the host installs it.
export default defineConfig({
  entry: ["index.ts", "setup-entry.ts"],
  format: ["esm"],
  dts: true,
  clean: true,
  sourcemap: true,
  target: "node22",
  outDir: "dist",
  external: [/^openclaw($|\/)/, "caspian-sdk"],
});
