import type { Connection } from "../../core/connection.ts"
import { httpLayer, type AdapterFetch } from "../http.ts"
import type { PlannedCall } from "../plan.ts"
import { recordingLayer } from "../recording.ts"
import {
  firstHeader,
  hmacSha256Hex,
  timingSafeEqualUtf8,
  configString,
} from "../util.ts"
import { planAck, planCommand } from "./execute.ts"
import { overlapKey } from "./ids.ts"
import { parseSlackUpdate } from "./parse.ts"

const spec = {
  name: "slack",
  parse: parseSlackUpdate,
  overlapKey,
  planAck: () => planAck(),
  planCommand,
}

export const slackLayer = (sink: PlannedCall[]) => recordingLayer(spec, sink)

export const slackHttpLayer = (fetchImpl?: AdapterFetch) =>
  httpLayer(spec, fetchImpl)

export const verifySlack = (
  body: string,
  headers: { readonly [key: string]: string },
  conn: Connection,
): boolean => {
  const secret = configString(conn.config, "signingSecret")
  if (secret.length === 0) {
    return true
  }
  const timestamp = firstHeader(headers, "X-Slack-Request-Timestamp")
  const got = firstHeader(headers, "X-Slack-Signature")
  const digest = hmacSha256Hex(secret, `v0:${timestamp}:${body}`)
  return timingSafeEqualUtf8(`v0=${digest}`, got)
}
