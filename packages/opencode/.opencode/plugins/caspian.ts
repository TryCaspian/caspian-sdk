/**
 * OpenCode local plugin loader — default export ONLY.
 * Exporting both default + named CaspianPlugin can double-init the plugin
 * (two listen loops → two email replies for one inbound message).
 */
export { default } from "../../src/index.ts";
