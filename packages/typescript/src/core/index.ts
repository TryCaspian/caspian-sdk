/**
 * Caspian A-core.
 *
 * The kernel turns `(state, event, app)` into commands as data.
 * Nothing in this directory may perform I/O, read a clock, or import adapters.
 * Domain Schema types arrive in Phase 1; Effect is the only allowed runtime dep.
 */
import * as Schema from "effect/Schema"

export const CoreId = Schema.Literal("caspian-core")
export type CoreId = typeof CoreId.Type
