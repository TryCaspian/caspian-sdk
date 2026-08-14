---
name: languages-as-libraries
description: How to ship a derived language, typed layer, or dialect as a library over an existing host toolchain instead of forking the compiler — reusing scoping, namespaces, modules, and analysis. Apply when embedding DSLs, adding typed layers, or designing extensible language/plugin systems. Based on "Languages as Libraries" (Tobin-Hochstadt, St-Amour, Culpepper, Flatt, Felleisen; PLDI'11) and the Racket extension model.
---

# Languages as Libraries

Based on *Languages as Libraries* (Tobin-Hochstadt, St-Amour, Culpepper, Flatt, Felleisen; PLDI'11) and the Racket extension model.

## Thesis

Programming language design benefits from constructs for extending the syntax and semantics of a host language. The goal is not only a reusable VM — it is an extensible host language that supports linguistic reuse so derived languages can reuse scoping, namespaces, modules, and compilers.

A derived language should be able to:

- reuse host scoping mechanisms
- lift host namespace management into the experimental language
- manipulate surface syntax and AST
- interpose new context-sensitive static semantics
- communicate static results to the backend
- ship as a library with no host-compiler fork

## Guy Steele's growth principle

> I need to design a language that can grow. — Guy Steele, 1998

Growing a language requires more than a reusable virtual machine and libraries; it demands extension mechanisms across phases of language implementation. Racket shows that with enough extension surface, even a sophisticated typed sister language can be a library.

## Racket extension arsenal (patterns to reuse)

### Macros

Macros are functions from syntax → syntax, run at compile time. Prefer hygienic macros so generated binders do not capture user code.

Use macros for:

- notational shorthands
- embedding DSLs
- attaching out-of-band metadata (types, effects, docs)

### Syntax objects

Treat host ASTs as first-class values with:

- constructors / accessors
- source locations
- syntax properties for out-of-band communication (types, annotations) without breaking host forms

### Local expansion

Expand user code to a small fixed core language before analysis. This lets a typechecker / optimizer understand programs written with arbitrary macros without cataloging every extension.

**Rule: reduce sugar → core forms → analyze core forms.**

### Modules as language choice

Each module specifies its language (e.g. `#lang typed/racket`). A language L is a library providing:

- bindings for the base environment (forms + values)
- a whole-module hook (`#%module-begin`) for context-sensitive module semantics

This is the key move: **language choice is per module, not per process.**

## Typed Racket as the reference architecture

Typed Racket demonstrates the full stack as libraries:

- Annotate bindings with types (syntax properties on reused `define` / `λ`)
- Context-sensitive whole-module typechecking via module begin
- Typecheck an extensible language by expanding to core first
- Persist types across separate compilation (emit compile-time declarations)
- Safe typed↔untyped linking via contracts generated from types
- Type-driven source-to-source optimization before the host backend

### Challenges this solves

| Challenge | Library technique |
|---|---|
| Types on untyped binding forms | syntax properties |
| Whole-module context | module-begin wrapper |
| Macros / unknown sugar | local-expand to core |
| Separate compilation | residualize type env into compiled module |
| Untyped interop | contracts at boundary; skip checks typed↔typed |
| Optimization | rewrite using validated types + unsafe specialized ops |

## Design recipe (apply outside Racket)

When adding a dialect / skill language / typed layer on TS/Python/Rust hosts:

1. **Reuse the host compiler** — do not fork
2. **Define a tiny core IR** — expand/desugar everything into it
3. **Attach metadata out-of-band** — attributes, JSDoc/TS types, Rust attributes, decorators
4. **Whole-unit analysis hook** — file/module/plugin transform
5. **Boundary contracts** — protect invariants when crossing into untyped/untrusted code
6. **Optimize by rewriting** once static info is proven

## Interop principles

- Typed modules exporting to untyped clients need dynamic checks
- Typed↔typed should avoid redundant checks
- Macros / generative code that escapes typed modules can break invariants — gate or contract them
- Separate compilation must rehydrate static environments

## Relation to other skills

- **dsl** — decide internal vs external; languages-as-libraries is the strongest path for internal DSLs
- **functional-dsl** — denotation/algebra first; this skill is how to embed that algebra into a real host toolchain
- **meta-minimal-languages** — keep the host small/stable so libraries (languages) can evolve faster than the core

## Review checklist

- Can this language ship as a package?
- Is the core IR small enough to analyze?
- Are host binding/scoping rules reused rather than reimplemented?
- Is static info persisted across compilation units?
- Are untrusted boundaries contracted?
- Are optimizations library rewrites, not compiler forks?
