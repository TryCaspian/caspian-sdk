---
name: taste
description: >-
  Domain-first store/port design for any codebase: product-shaped APIs,
  swappable backends, schema at boundaries, one vocabulary catalog (derive
  don’t re-author), no infrastructure names on the domain surface. Use when
  adding or refactoring persistence, cache, policy resolve, registries, or
  anything that might leak Redis/SQL/HTTP/SDK names into shared contracts.
  Taste: one fact one place, HOF as shared ritual not twin wrappers, call-site
  clarity, dumb engines, challenge early / cut ruthlessly, DX (cascading
  IntelliSense) as correctness, policy resolve → push-down query plan (not
  load-all-then-filter), no hardcodes when dims/rows already name the filter.
---

# Domain-first stores

Portable agent law for persistence, cache, policy documents, and similar ports.
If the repo has its own `AGENTS.md` overlay, read that after this file and
prefer project overrides for paths and house libraries.

## When this applies

Designing or changing shared domain surfaces: stores, registries, policy
resolve, CAS/publish, desired-set membership, or multi-service clients that
must not fork wire APIs.

## Taste (abstract)

1. **One fact, one place.** Vocabulary and policy live in small catalogs
   (dimensions, fields, families). Everything else is **derived**. If adding
   one concept means editing three sibling types (`FooConfig` + `FooContext` +
   `FooDims`), delete the twins.

2. **Indexed ideas over parallel nouns.** Prefer catalog row + allowed dims →
   context/result over re-authored per-family Structs. Types should cascade
   from the catalog.

3. **HOF means shared ritual, not twin wrappers.** Three helpers that only
   inject the same default are copy. Lift once when compose actually repeats;
   otherwise **inline**. Prefer `decodeFamily(family, …)` over `decodeFoo` /
   `decodeBar` / `decodeBaz`.

4. **Call sites stay obvious.** Pass raw fields; schema owns null/empty. Don’t
   hide a one-liner behind a named helper. One context type: scalar or
   non-empty array per dim; match with equality or ∈. Closed dims declare
   enums on the registry; open dims stay free strings.

5. **Engine stays dumb; boundaries stay sharp.** Cascade/match with a simple
   priority rule — no expand/desugar pass. Allowlists and IntelliSense live at
   catalog / publish / family resolve — not a second “pass a Policy bag” API.
   Resolve **on use** (ports); never freeze knobs at `create*` / `make*` boot.

6. **Challenge early, cut ruthlessly.** Prefer deleting a layer to polishing a
   wrong one. Redundant Structs and family-specific context exports are smell,
   not thoroughness.

7. **DX is part of correctness.** If IntelliSense doesn’t follow a new dim or
   field, the type model failed — even when runtime works. Adding a dim =
   registry row; adding a field = one catalog row (+ family key list if needed).

## Taste (call sites & policy)

1. **Call site stays clean.** Decode at the family/resolve boundary — not
   scattered `??` / `=== "" ? null` / dual APIs for the same policy.

2. **Invariants at the boundary.** Decode once; the rest of the system only
   sees a clean shape. Schema bounds are the ceiling — don’t re-clamp the same
   knobs via env at boot.

3. **Resolve from real context.** Hot path uses identity dims that exist
   (`fooId` / `bar` / `region` / …) against defaults + overrides — not a bare
   env-only context when richer identity is available, and not a parallel
   Policy injection. Example:
   `resolve.foo({ env: "published", fooId, bar })`.

4. **Pass the whole port, not plucked fields.** Wire `resolve.foo` (or the
   whole resolve bag) into consumers — don’t extract `fooLimit` at `main` /
   `server` and pass a number.

5. **One concern, one return.** Boot/open helpers return the store (or one
   domain value) — not `{ store, resolveEverything }`. Callers compose resolve
   ports at the edge.

6. **Share the ritual, don’t twin it.** One module per compose; no
   service-local copies of the same decode/match/apply.

7. **Defaults everywhere, scoped when stated.** Defaults are the base case;
   dimensions/overrides only matter when specified.

8. **Ugly in adapters/scripts, not the server.** Seeds and fixtures are
   scripts; empty required config → fail loud.

9. **Explain by flow, not jargon.** Show a concrete resolve example
   (`defaults` + matching override → result).

10. **Live day one, minimal now.** Production-minded (version/revert, HA) but
    ruthlessly cut scope. Ship the flexible core; defer authoring UI / extra
    domains until needed.

11. **House schema library owns Encoded/Type.** Prefer derived
    `FamilyContextEncoded<"foo">` over hand-rolled `{ fooId: string; … }` bags.

12. **Policy resolve drives the query — don’t filter the world in JS.**
    Derive a **conservative** DB/search plan from the same resolve port
    (probe closed dims like `bar` from the registry), push
    `WHERE` / `ORDER` / `LIMIT` (or equivalent), then re-resolve with full row
    context (`fooId`, …) only on the small result for open-dim overrides.
    Example: `planFooLoad(resolveFoo)` → SQL filters + `LIMIT` → JS finish for
    per-`fooId` keep/drop.

13. **No hardcodes when the row or closed enum already names it.** Don’t bake
    `bar = 'qux'` into loaders that should follow the registry. Prefer
    `bar IN (enabled…)` from resolve, or no `bar` predicate when the plan is
    “all enabled bars.” Hardcodes fight multi-value dims and make admin
    overrides lie.

14. **Prefilter is conservative; finish pass is exact.** Under-fetching because
    the plan guessed wrong is worse than a slightly larger `LIMIT`. Closed-dim
    floors/size from resolve are safe bounds; open dims (`fooId`) stay in the
    post-load pass. Skip the query when every probed context resolves disabled.

## Hard rules

1. **Domain API first.** Public names describe product concepts
   (`getFields`, `writeAll`, `publish`, `retain`), never wire commands
   (`hgetall`, `INSERT`, `GetItem`) or vendor products in types, factories,
   errors, or domain comments.

2. **Backends are swappable.** Runtime adapters (cache, SQL, HTTP, SDK) vs
   in-process backends for unit tests. Do not bake wire commands into the Tag /
   port interface.

3. **Service shape.** Prefer an explicit service interface + tagged errors.
   Factories return the service / Layer — not `createVendorX(client)`.

4. **Wire only at the edge.** Adapters map infrastructure → domain. Scripts,
   pipelines, and SDK calls stay inside adapters — not on public contract
   exports’ names.

5. **One shared module per concern.** Do not fork the same store into two
   services. Put it in a shared package (`packages/domain`, `packages/contracts`, …).

6. **No duplicate client interfaces.** If the domain service already defines
   ops, don’t redefine the same Promise surface under another name in a
   service package.

7. **Side channels stay adapters.** Pub/sub channel strings, queue names, and
   publish transport calls are adapter details. Domain may expose
   `publish → boolean` and an optional sink — not transport identifiers on the
   Tag.

8. **Comments stay product-shaped.** Don’t document “Redis-backed” /
   “Dynamo-backed” on domain constructors. Say what the backend *does*
   (in-process cache, scripted CAS).
