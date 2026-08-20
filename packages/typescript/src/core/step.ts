/**
 * step — the kernel's pure function.
 *
 * step(state, event, app) → commands as data.
 * No I/O, no clock, no randomness.
 */
import type { App, Rule } from "./app.ts"
import type { Command } from "./commands.ts"
import type { Event } from "./events.ts"
import {
  idleOverlapState,
  overlapTransition,
  type OverlapState,
} from "./overlap.ts"
import { evaluate } from "./predicates.ts"

export type StepState = {
  readonly overlap: { readonly [key: string]: OverlapState }
}

export const emptyStepState: StepState = { overlap: {} }

export type StepDecision = "execute" | "enqueue" | "drop" | "unmatched"

export type StepResult = {
  readonly decision: StepDecision
  readonly commands: ReadonlyArray<Command>
  readonly matched_rule: Rule | undefined
  readonly skipped_count: number
  readonly dropped: boolean
  readonly state: StepState
}

const getOverlap = (state: StepState, key: string): OverlapState =>
  state.overlap[key] ?? idleOverlapState

const setOverlap = (state: StepState, key: string, next: OverlapState): StepState => ({
  overlap: { ...state.overlap, [key]: next },
})

const unmatched = (state: StepState): StepResult => ({
  decision: "unmatched",
  commands: [],
  matched_rule: undefined,
  skipped_count: 0,
  dropped: false,
  state,
})

export const step = (
  state: StepState,
  event: Event,
  app: App,
  options: {
    readonly channelName?: string
    readonly overlapKey?: string
  } = {},
): StepResult => {
  const channelName = options.channelName ?? ""
  const key = options.overlapKey ?? event.thread_id

  for (const rule of app.rules) {
    if (!evaluate(rule.predicate, event, channelName)) {
      continue
    }

    const transition = overlapTransition(
      getOverlap(state, key),
      rule.overlap.policy,
      rule.overlap.bound,
    )
    const next = setOverlap(state, key, transition.new_state)

    switch (transition.decision) {
      case "drop":
        return {
          decision: "drop",
          commands: [],
          matched_rule: rule,
          skipped_count: 0,
          dropped: true,
          state: next,
        }
      case "enqueue":
        return {
          decision: "enqueue",
          commands: [],
          matched_rule: rule,
          skipped_count: 0,
          dropped: false,
          state: next,
        }
      case "execute":
        return {
          decision: "execute",
          commands: [
            ...(rule.ack === ""
              ? []
              : [
                  {
                    tag: "Post" as const,
                    thread_id: event.thread_id,
                    text: rule.ack,
                    actions: [],
                    standalone: false,
                  },
                ]),
            { tag: "Typing" as const, thread_id: event.thread_id },
            { tag: "Host" as const, handler_id: rule.handler_id },
          ],
          matched_rule: rule,
          skipped_count: transition.skipped_count,
          dropped: false,
          state: next,
        }
    }
  }

  return unmatched(state)
}
