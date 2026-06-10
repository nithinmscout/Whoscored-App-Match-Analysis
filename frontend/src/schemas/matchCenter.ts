import { z } from 'zod'

/**
 * Nullable number used across volatile Opta / WhoScored payloads.
 * Scrapers often emit null, empty string, or undefined for coordinates.
 */
const NullableNumber = z.union([z.number(), z.null()]).optional().transform((value) => {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
})

const NullableString = z.union([z.string(), z.null()]).optional().transform((value) => {
  return typeof value === 'string' ? value : null
})

/**
 * Opta qualifiers are inconsistent across match payloads.
 * Sometimes "type" is a number, sometimes an object, sometimes displayName only.
 */
export const OptaQualifierSchema = z.object({
  type: z.union([
    z.number(),
    z.object({
      value: z.number().optional(),
      displayName: z.string().optional(),
      name: z.string().optional(),
    }),
  ]).optional(),
  value: z.union([z.string(), z.number(), z.boolean(), z.null()]).optional(),
  displayName: z.string().optional(),
  name: z.string().optional(),
}).passthrough()

export const TeamRefSchema = z.object({
  teamId: z.number().optional(),
  id: z.number().optional(),
  name: z.string().optional(),
}).passthrough()

export const PlayerRefSchema = z.object({
  playerId: z.number().optional(),
  id: z.number().optional(),
  name: z.string().optional(),
  shirtNo: z.number().optional(),
}).passthrough()

export const MatchEventSchema = z.object({
  id: z.number().optional(),
  eventId: z.number().optional(),

  teamId: z.number().optional(),
  playerId: z.number().optional(),

  team: TeamRefSchema.optional(),
  player: PlayerRefSchema.optional(),

  period: z.union([z.number(), z.string()]).optional(),
  minute: z.number().optional(),
  second: z.number().optional(),
  expandedMinute: z.number().optional(),

  type: z.union([
    z.string(),
    z.object({
      value: z.number().optional(),
      displayName: z.string().optional(),
      name: z.string().optional(),
    }),
  ]).optional(),

  outcomeType: z.union([
    z.string(),
    z.object({
      value: z.number().optional(),
      displayName: z.string().optional(),
      name: z.string().optional(),
    }),
  ]).optional(),

  x: NullableNumber,
  y: NullableNumber,
  endX: NullableNumber,
  endY: NullableNumber,
  blockedX: NullableNumber,
  blockedY: NullableNumber,
  goalMouthY: NullableNumber,
  goalMouthZ: NullableNumber,

  isTouch: z.boolean().optional(),
  isShot: z.boolean().optional(),
  isGoal: z.boolean().optional(),

  qualifiers: z.array(OptaQualifierSchema).optional().default([]),
}).passthrough()

export const MatchPlayerSchema = z.object({
  playerId: z.number(),
  teamId: z.number().optional(),
  name: z.string().optional(),
  shirtNo: z.number().optional(),
  position: NullableString,
  isFirstEleven: z.boolean().optional(),
  isManOfTheMatch: z.boolean().optional(),
  minutesPlayed: z.number().optional(),
}).passthrough()

export const MatchTeamSchema = z.object({
  teamId: z.number(),
  name: z.string().optional(),
  players: z.array(MatchPlayerSchema).optional().default([]),
}).passthrough()

export const MatchCentreDataSchema = z.object({
  matchId: z.number().optional(),
  startTime: z.string().optional(),
  score: z.string().optional(),
  venueName: z.string().optional(),
  referee: NullableString,

  home: MatchTeamSchema.optional(),
  away: MatchTeamSchema.optional(),

  events: z.array(MatchEventSchema).default([]),

  /**
   * Some payloads expose both flat and team nested player arrays.
   */
  players: z.array(MatchPlayerSchema).optional().default([]),
}).passthrough()

export type MatchCentreData = z.infer<typeof MatchCentreDataSchema>
export type MatchEvent = z.infer<typeof MatchEventSchema>
export type MatchPlayer = z.infer<typeof MatchPlayerSchema>
export type MatchTeam = z.infer<typeof MatchTeamSchema>
export type OptaQualifier = z.infer<typeof OptaQualifierSchema>

export function createEmptyMatchCentreData(): MatchCentreData {
  return {
    matchId: -1,
    startTime: '',
    score: '',
    venueName: '',
    referee: null,
    home: {
      teamId: -1,
      name: 'Unknown home team',
      players: [],
    },
    away: {
      teamId: -1,
      name: 'Unknown away team',
      players: [],
    },
    events: [],
    players: [],
  }
}

export interface SafeParseResult<T> {
  ok: boolean
  data: T
  issues: string[]
}

/**
 * Safe ingestion boundary.
 * Never throws.
 * Logs exact schema failure paths.
 * Returns a standard fallback object.
 */
export function parseMatchCentreDataSafely(input: unknown): SafeParseResult<MatchCentreData> {
  const parsed = MatchCentreDataSchema.safeParse(input)

  if (parsed.success) {
    return {
      ok: true,
      data: parsed.data,
      issues: [],
    }
  }

  const issues = parsed.error.issues.map((issue) => {
    const path = issue.path.length > 0 ? issue.path.join('.') : 'root'
    return `${path}: ${issue.message}`
  })

  console.error('[matchCentreData] schema validation failed', issues)

  return {
    ok: false,
    data: createEmptyMatchCentreData(),
    issues,
  }
}
