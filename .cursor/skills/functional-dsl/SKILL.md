---
name: functional-dsl
description: Design DSLs the functional way — as pure data structures and composable functions, with the denotation (meaning) defined before syntax and effects isolated to interpreters. Apply when proposing a new DSL, reviewing an existing one, or modeling agent workflows, tool plans, and program graphs. Use the Zen and review criteria as hard gates.
---

# Functional DSLs

In functional programming (FP), language design isn't viewed as building a parser—it's viewed as algebraic design. FP design principles treat a DSL as a set of pure data structures and composable functions.

## The Zen of Functional DSLs

If you synthesized the FP community's core tenets into a Zen of Functional DSLs, it would read like this:

- Programs are data; interpreters are functions.
- Make illegal states unrepresentable.
- Parse, don't validate.
- Design the denotation (meaning) before the syntax.
- Decouple the domain algebra from its execution.
- If it doesn't terminate, it's a general-purpose language, not a DSL.
- Composition over execution.

Apply these as hard review criteria when proposing a new DSL or revising an existing one.

## 1. Conal Elliott: Denotational Design

Conal Elliott (pioneer of Functional Reactive Programming) advocates for designing software starting purely from mathematical semantics.

- **Read:** Denotational Design: From Meanings to Programs
- **Philosophy:** Don't start with AST nodes, classes, or parser rules. Start with the abstract mathematical meaning of your domain.
- **Example:** if you're building an animation DSL, a "Movement" is simply a pure function `Time -> Location`. Once you define the math, your DSL syntax and operations naturally fall out of standard mathematical laws (like Monoids and Functors).

**Practice**

- Write the semantic type of each domain concept first.
- List the laws (identity, associativity, composition).
- Only then invent syntax / host APIs that inhabit those meanings.

## 2. Alexis King: "Parse, Don't Validate"

Alexis King's famous 2019 essay is considered required reading across the functional programming landscape.

- **Read:** Parse, Don't Validate
- **Philosophy:** Never check data with booleans (validation) and pass raw data downstream. Instead, write a parser that transforms loose, raw input into a tight, domain-specific type. By pushing validation into the parsing phase, invalid states become unrepresentable by construction in the rest of your DSL engine.

**Practice**

- Prefer `Result<DomainAst, ParseError>` at the boundary
- Ban "validated flag" / parallel boolean checks deeper in the stack
- Use branded / refined types for IDs, amounts, tool names, etc.

## 3. Oleg Kiselyov: Typed Tagless Final Embedding

Oleg Kiselyov introduced one of the most influential patterns for constructing embedded DSLs.

- **Read:** Typed Tagless Final Interpreters
- **Philosophy:** Traditional DSLs build an explicit AST (Initial Encoding) and then traverse it. "Tagless Final" expresses the DSL as a set of functions or typeclasses instead. This lets you write code in your DSL once, and instantly run multiple pluggable interpreters against it (e.g., an evaluator, an optimizer, a type checker, or a printer) without modifying the DSL grammar or re-traversing trees.

**Practice**

- Define a capability interface / typeclass for the algebra
- Implement multiple interpreters: eval, pretty-print, simulate, optimize
- Prefer final encoding when you need many backends; prefer initial/Free when you need introspection, serialization, or time-travel

## 4. Gabriel Gonzalez: Total Functional Programming & Dhall

Gabriel Gonzalez created Dhall, a functional, programmable configuration language built to replace YAML and JSON without allowing infinite loops or security exploits.

- **Read:** Dhall Language Design & Philosophy
- **Philosophy:** Total Programming. A great functional DSL should guarantee termination (it is deliberately not Turing-complete). Users should be able to write functions, imports, and abstractions in the DSL, but the host environment must be 100% guaranteed that the DSL script will never hang, crash, or access the file system directly.

**Practice**

- For agent config / policy / skill metadata: prefer total languages
- Keep effects in the host interpreter, not in the config DSL
- Recursion, arbitrary loops, and ambient I/O are host concerns

## 5. Rúnar Bjarnason & Paul Chiusano: Algebra & Free Monads

Co-authors of *Functional Programming in Scala* ("The Red Book"), their work popularized using algebraic structures and Free Monads to construct DSLs.

- **Read:** Functional Programming in Scala (Manning)
- **Philosophy:** A program is just a data structure. You write a DSL by defining an "Algebra" (a set of pure data constructors representing operations). Building a script in your DSL doesn't execute anything; it merely builds an immutable description of intent. You then pass that data structure to a separate, isolated interpreter function that performs the actual computation or side effects.

**Practice**

```
Algebra (pure constructors)
    -> Program value (immutable description)
        -> Interpreter(s) (effects, optimization, tracing)
```

This maps cleanly to agent workflows, tool plans, and Effect-style program graphs.

## Design workflow (use this order)

1. **Denotation** — mathematical meaning / types / laws
2. **Algebra** — operations as data or typeclass methods
3. **Parse** — raw input → domain types (no boolean validation later)
4. **Interpreters** — eval, optimize, explain, dry-run, execute
5. **Syntax last** — host fluent API or external surface that reveals the algebra

## Mapping onto repos in context

| Repo | Functional-DSL lens |
|---|---|
| Effect | Programs-as-values; interpreters / layers; composition over execution |
| Smithers | Durable workflow as data + observable interpreters |
| Composio / Treg | Tool algebras; parse tool schemas; separate auth/execution |
| Centaur | Tools/workflows/skills as plugins over a control-plane interpreter |
| Mastra / Agno | Agent platform algebras; keep domain intent separate from runtime |

## Review questions

- Can illegal states be constructed?
- Is meaning defined before syntax?
- Can the same program run under eval / dry-run / optimize interpreters?
- Does the DSL guarantee termination where it should?
- Are effects isolated to interpreters?
