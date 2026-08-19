/**
 * Catalog is the phone book. It does not send.
 *
 * Lookup returns Effect so a missing id is UsageError data, not a throw.
 */
import * as Effect from "effect/Effect"
import catalogJson from "../../../vectors/cli_catalog.json" with { type: "json" }
import { UsageError } from "./errors.ts"

export type CatalogEntry = {
  readonly id: string
  readonly tool: string
  readonly command_tag: string
  readonly summary: string
  readonly channel?: string
  readonly method?: string
}

const CATALOG: ReadonlyArray<CatalogEntry> =
  catalogJson as ReadonlyArray<CatalogEntry>

export const loadCatalog = (): ReadonlyArray<CatalogEntry> => CATALOG

export const getCatalog = (
  id: string,
): Effect.Effect<CatalogEntry, UsageError> => {
  const entry = CATALOG.find((row) => row.id === id)
  if (entry === undefined) {
    return Effect.fail(
      new UsageError({
        reason: `unknown id ${JSON.stringify(id)}; caspian catalog search …`,
      }),
    )
  }
  return Effect.succeed(entry)
}

export const searchCatalog = (query: string): ReadonlyArray<CatalogEntry> => {
  const words = query
    .split(/\s+/)
    .map((word) => word.toLowerCase())
    .filter((word) => word.length > 2)
  if (words.length === 0) return loadCatalog()
  return CATALOG.filter((entry) => {
    const hay = Object.values(entry).join(" ").toLowerCase()
    return words.every((word) => hay.includes(word))
  })
}
