import { httpLayer, type AdapterFetch } from "../http.ts"
import type { PlannedCall } from "../plan.ts"
import { recordingLayer } from "../recording.ts"
import { planAck, planCommand } from "./execute.ts"
import { overlapKey } from "./ids.ts"
import { parseDiscordUpdate } from "./parse.ts"

const spec = {
  name: "discord",
  parse: parseDiscordUpdate,
  overlapKey,
  planAck: (event: Parameters<typeof planAck>[0]) => planAck(event),
  planCommand,
}

export const discordLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)

export const discordHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)
