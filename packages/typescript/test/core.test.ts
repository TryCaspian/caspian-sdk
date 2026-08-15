import { expect, test } from "bun:test"
import * as Schema from "effect/Schema"
import { CoreId } from "../src/core/index.ts"

test("core is an Effect Schema module with no I/O", () => {
  expect(Schema.decodeUnknownSync(CoreId)("caspian-core")).toBe("caspian-core")
})
