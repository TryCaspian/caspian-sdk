/** @type {import("dependency-cruiser").IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "core-no-caspian-layers",
      comment:
        "A-core must not import adapters, provision, facade, tools, or interpreters.",
      severity: "error",
      from: { path: "^src/core" },
      to: { path: "^src/(adapters|provision|facade|tools|interpreters)" },
    },
    {
      name: "core-no-node-builtins",
      comment:
        "A-core is decidable without a network, clock, filesystem, or node: imports.",
      severity: "error",
      from: { path: "^src/core" },
      to: { dependencyTypes: ["core"] },
    },
    {
      name: "core-only-effect-npm",
      comment: "The only npm package core may import is effect.",
      severity: "error",
      from: { path: "^src/core" },
      to: {
        dependencyTypes: ["npm", "npm-dev", "npm-no-pkg"],
        pathNot: "node_modules/effect(/|$)",
      },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default", "types"],
    },
  },
}
