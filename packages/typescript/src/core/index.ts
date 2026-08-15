/**
 * Caspian A-core.
 *
 * The kernel turns `(state, event, app)` into commands as data.
 * Nothing in this directory may perform I/O, read a clock, or import adapters.
 * Decode at the boundary; the kernel only ever sees Schema types.
 */
export * from "./app.ts"
export * from "./commands.ts"
export * from "./connection.ts"
export * from "./errors.ts"
export * from "./events.ts"
export { ConnectionId, ThreadId } from "./ids.ts"
export { Json, JsonObject } from "./json.ts"
export * from "./predicates.ts"

import { App } from "./app.ts"
import { Command } from "./commands.ts"
import { Event } from "./events.ts"
import { decodeStrict } from "./parse.ts"
import { Predicate } from "./predicates.ts"

export const decodeEvent = decodeStrict(Event)
export const decodeApp = decodeStrict(App)
export const decodeCommand = decodeStrict(Command)
export const decodePredicate = decodeStrict(Predicate)
