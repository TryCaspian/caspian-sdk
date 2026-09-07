---
name: sdk-reliability
description: A framework for making SDKs, libraries, and developer tools 10X more reliable. Apply it when designing new APIs, reviewing changes to core libraries, or diagnosing failures. The functional-programming stance runs throughout — model failure as data, keep the core pure, push effects to the edges, and let the type system enforce the invariants that runbooks can't.
---

# SDK & Dev Tools Reliability

A framework for making SDKs, libraries, and developer tools 10X more reliable. Apply it when designing new APIs, reviewing changes to core libraries, or diagnosing failures. The functional programming stance runs throughout: model failure as data, keep the core pure, push effects to the edges, and let the type system enforce the invariants that runbooks can't.

## A) Tenets for 10X Reliability

- **Abundant redundancy, active-active, instant failover / graceful degradation.** Every effectful dependency (network, disk, subprocess, remote API) sits behind an interface with at least one alternative interpreter.
- **Critical path — 100X strong, utterly simple, with big limits.** The core combinators and dispatch logic must be small, pure, total, and boring.
- **Reduce blast radius.** Set boundaries and limits between modules, plugins, and consumers. Isolation is a design-time property: separate effect scopes, separate resource pools, no shared mutable state.
- **Auto anomaly detection, auto RCA; failure prevention and early detection.** Prefer prevention via types: make illegal states unrepresentable so whole failure classes never reach runtime.
- **Dynamic, model-based capacity allocation; zero bottlenecks.** Bounded queues, backpressure-aware streams, explicit pool sizing.
- **Mandatory staggering, A/B testing, and rollback ability for all changes** — code, config, and dependency upgrades alike.
- **Test the limits** — load, failure, and chaos testing with real workloads. Purity makes this cheap: swap in a chaos interpreter.
- **RCA/post-mortem process done with rigour and made sacred.** Every RCA ends by hardening the critical path, not just patching the symptom.
- **Set ambitious SLOs for the SDK itself** — cold-start time, p99 call overhead, error-surface completeness. Constantly measure and improve.
- **Collective ownership of reliability.** Everyone who touches the library owns its failure modes; culture reinforces first-principles and systems thinking across teams and roles.

## B) The Three Causes of Reliability Issues

Every reliability issue traces to one of three causes. Diagnose which one first, then apply its remedies.

### 1. Fault — failures from wear and tear or uncertain external events

Flaky networks, dying processes, corrupted caches, upstream API outages.

**Solution: redundancy, expressed as swappable interpreters.**

- **Failover** — switch to a symmetric setup with no degradation of features or performance. This is the preferred mechanism. In FP terms: the same pure program runs against an alternative interpreter of the same interface (typeclass instance, module functor, or handler for an effect).
- **Fallback** — switch to a different kind of system, typically with degraded performance (e.g. remote resolver → local cache, incremental engine → full rebuild). Less preferred; make the degradation explicit in the return type so callers can see it.
- **Partial parts turn-off** — a switchboard to disable less-critical features (telemetry, suggestions, prefetching) while the core keeps working.

**Critical path implications:**

- The health check / heartbeat is in the critical path.
- The switch that performs the failover is also in the critical path.

**Best practices:**

- **Fail fast.** Detect the failure and switch if there is redundancy. Model failure as a value (Result/Either/error ADT), never as a thrown exception escaping the API surface — a failure you can pattern-match on is a failure you can route around.
- Make every function **total** over its declared input type: no partial matches, no undefined branches, no panics on valid inputs.
- For the overall tool, prevent complete failure by choosing a degraded experience over none.
- Reduce blast radius with small, independent, end-to-end components — no big monolithic stage in the critical path whose failure takes everything down.

### 2. Capacity — real resource exhaustion or artificial limits hit

File-descriptor exhaustion, thread-pool starvation, unbounded memory from strict evaluation of large inputs, connection-pool caps.

**Solutions:**

- **Rate limiting and resource limiting built into the SDK client itself**, as pure policy values (composable limiter combinators) interpreted at the effect boundary — consumers shouldn't have to bolt these on.
- **Backpressure by construction.** Prefer pull-based streams and lazy / incremental evaluation over loading whole datasets; bounded channels over unbounded queues.
- **Granular capacity monitoring and prediction of subcomponents**, not just the whole: per-pool, per-plugin, per-worker.
- **Per-consumer capacity provisioning and alerts** — quotas per API key, per plugin, per workspace, configurable via self-service.
- **Optimized critical path with cheap, abundant capacity (10–100X).** Dispatchers, routers, status/read APIs, and rate limiters must cost almost nothing. Persistent (immutable) data structures with sharing keep hot-path allocation predictable.
- **Load testing to identify bottlenecks.** Test the limits of scaling.
- **Audit artificial limits** — low default connection caps, small buffer sizes, conservative pool defaults — before they surprise users at scale.

### 3. Change — code, config, and dependency changes

The dominant cause for SDKs: a new release, a config default flip, a transitive dependency bump.

**Solutions:**

- **Staggered rollout of releases:** canary tags, pre-release channels, percentage-based config rollout for remotely-configured tools.
- **A/B testing while staggering and post-release** — compare error rates and latency between versions before promoting.
- **CI/CD checks to stop errors before production.** This is where FP pays off most: the compiler is your first reliability gate.
  - Type checks, exhaustiveness checks, and lint rules as compiler plugins.
  - Property-based tests for the algebraic laws your combinators claim (identity, associativity, round-tripping of codecs).
  - Deterministic golden/snapshot tests — trivial when the core is referentially transparent.
  - Semver enforcement: automated API-diff checks so breaking changes can't ship as a patch.
- **Non-critical-path changes:** the framework and critical path should check and contain serious issues, so changes outside the critical path cannot cause serious damage.
- **Critical-path changes:** thorough code review, mandatory tests, and a slower rollout track.

## C) The Yin/Yang of Reliability and the Critical Path

Any problem is ultimately a weakness in the critical path. Reliability work has a dual structure:

- **AD — Abundance and Decentralization.** Redundancy, diversity, fault tolerance; isolation, blast-radius reduction, limits; abundance of capacity, dependence on widely available resources. Pursue this side everywhere you can.
- **CP — the Critical Path.** The scarce, central things you cannot decentralize away: the switch that manages redundancy, the core combinator library, the effect interpreter/runtime, routers, rate limiters, error monitors, the type checker and compiler plugins.

CP is narrow and provides focus — and fixing it points you back to AD. The discipline: keep fixing the critical path and make sure it never fails. Each fix removes a whole category of issues, not just the one in front of you.

Example: a tool went down when its backing store failed. The critical path was (a) the failover switch that should have moved traffic to the replica — but the replica lacked capacity — and (b) the simple system that ensures capacity headroom, alerts when it's missing (in a dual setup, utilization should stay below 50%), and balances load. Both were CP weaknesses; fixing them fixes every future incident of this shape.

### Patterns of the Critical Path

CP items have a centralizing pattern. Items marked (E) need strong, unified error monitoring.

- **Failover switch (E)** — interpreter/backend selection logic.
- **Load balancer, auto scaler (E)** — worker-pool dispatch, scheduler.
- **Rate limiting and isolation sharding** — separate resource scopes per consumer, per plugin, per workspace.
- **Critical/non-critical divider (E)** —
  - queue non-critical work to async;
  - on errors, drop non-critical work;
  - on low capacity, turn off non-critical features.
- **Workflow / task executor (E)** — the effect runtime that interprets the pure program description.
- **Staggerer and A/B tester (E)** — release-channel and rollout logic.
- **(E) Error unification, unified monitoring and alerting:**
  - a single error ADT at the API surface — every failure is one of a known, exhaustively-matched set of constructors;
  - health checks for failovers;
  - capacity checks for load balancing and auto scaling;
  - unified 5XX / task-failure aggregation;
  - multi-dimensional deviation checkers on A/B metrics — detect anomalies that escape the CP's static guarantees.
- **CI/CD and compile-time checks — the FP-native CP guards:**
  - no config read outside the config effect;
  - no I/O outside the effect boundary (enforced by types);
  - no public API endpoint outside authentication;
  - no unhandled error constructor (exhaustiveness);
  - alerts for gaps, e.g. "no route without a rate limiter".
- **Other core libraries** — the effect system / runtime, the core combinator and codec library, the authentication module. Treat every change to these as a CP change.

## Applying This Skill

When reviewing or designing, work through this checklist:

- Which of the three causes (fault / capacity / change) does each risk map to, and is its remedy in place?
- Is failure modeled as data (Result/error ADT) everywhere on the public API surface? Are all functions total?
- Is every effectful dependency behind an interface with a symmetric failover interpreter — and a degraded fallback where symmetry is impossible?
- Is the critical path identified, minimal, pure where possible, and 10–100X overprovisioned?
- Do compile-time checks (types, exhaustiveness, lint plugins, API diff) block the failure classes that matter?
- Are limits, quotas, and backpressure explicit and bounded — no unbounded queue, buffer, or recursion on the hot path?
- Can every change be staggered, A/B compared, and rolled back?
- Do property-based and chaos tests (via a chaos interpreter) exercise the claimed laws and failure modes?
