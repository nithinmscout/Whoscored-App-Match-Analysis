export type PitchTone = 'cyan' | 'violet' | 'amber' | 'green' | 'red' | 'neutral'
export type EventKind =
  | 'shot'
  | 'goal'
  | 'pass'
  | 'cross'
  | 'carry'
  | 'take_on'
  | 'tackle'
  | 'interception'
  | 'recovery'
  | 'block'
  | 'clearance'
  | 'duel'
  | 'foul'
  | 'set_piece'
  | 'touch'
  | 'other'

export type EventShape = 'circle' | 'diamond' | 'square' | 'triangle' | 'star' | 'cross' | 'ring'

export interface EventGlyphSpec {
  label: string
  shape: EventShape
  radius: number
  strokeWidth: number
  opacity: number
  drawPriority: number
}

export interface PitchPoint {
  x?: number | string | null
  y?: number | string | null
  end_x?: number | string | null
  end_y?: number | string | null
  start_x?: number | string | null
  start_y?: number | string | null
  event_type?: string | null
  type?: string | null
  outcome_type?: string | null
  player?: string | null
  team?: string | null
  minute?: number | string | null
  xg?: number | string | null
  is_goal?: boolean | string | number | null
  is_shot?: boolean | string | number | null
  is_success?: boolean | string | number | null
  successful?: boolean | string | number | null
  led_to_shot?: boolean | string | number | null
  label?: string | null
  order?: number | string | null
  event_kind?: string | null
  is_carry?: boolean | string | number | null
  is_inferred_carry?: boolean | string | number | null
  is_take_on?: boolean | string | number | null
  is_provider_take_on?: boolean | string | number | null
}

export const PITCH_STANDARD = {
  provider: 'WhoScored Opta percentage pitch',
  sourceXRange: [0, 100] as const,
  sourceYRange: [0, 100] as const,
  viewBoxWidth: 105,
  viewBoxHeight: 68,
  fieldPadding: 2,
  displayAspectRatio: 105 / 68,
  direction: 'left to right when not flipped',
}

export const GOAL_MOUTH_STANDARD = {
  horizontalRange: [0, 100] as const,
  verticalRange: [0, 100] as const,
  ratio: 3,
  horizontalMeaning: '0 shooter left post, 100 shooter right post',
  verticalMeaning: '0 grass line, 100 crossbar',
}

export const PITCH_LANES = [
  { key: 'left_wide', label: 'Left wide', yMin: 0, yMax: 18, family: 'wide' },
  { key: 'left_half_space', label: 'Left half space', yMin: 18, yMax: 38, family: 'half_space' },
  { key: 'central', label: 'Central', yMin: 38, yMax: 62, family: 'central' },
  { key: 'right_half_space', label: 'Right half space', yMin: 62, yMax: 82, family: 'half_space' },
  { key: 'right_wide', label: 'Right wide', yMin: 82, yMax: 100, family: 'wide' },
]

export const SIMPLE_LANES = [
  { key: 'left', label: 'Left', yMin: 0, yMax: 33.333 },
  { key: 'central', label: 'Centre', yMin: 33.333, yMax: 66.667 },
  { key: 'right', label: 'Right', yMin: 66.667, yMax: 100 },
]

export const TONE_COLOURS: Record<PitchTone, { primary: string; soft: string; faint: string; text: string }> = {
  cyan: {
    primary: 'rgba(45,216,233,0.96)',
    soft: 'rgba(45,216,233,0.40)',
    faint: 'rgba(45,216,233,0.12)',
    text: '#9beaf2',
  },
  violet: {
    primary: 'rgba(167,139,250,0.96)',
    soft: 'rgba(167,139,250,0.42)',
    faint: 'rgba(167,139,250,0.13)',
    text: '#c9b9ff',
  },
  amber: {
    primary: 'rgba(245,158,11,0.96)',
    soft: 'rgba(245,158,11,0.42)',
    faint: 'rgba(245,158,11,0.13)',
    text: '#facc7a',
  },
  green: {
    primary: 'rgba(34,197,94,0.96)',
    soft: 'rgba(34,197,94,0.42)',
    faint: 'rgba(34,197,94,0.13)',
    text: '#86efac',
  },
  red: {
    primary: 'rgba(239,68,68,0.96)',
    soft: 'rgba(239,68,68,0.42)',
    faint: 'rgba(239,68,68,0.13)',
    text: '#fca5a5',
  },
  neutral: {
    primary: 'rgba(226,232,240,0.88)',
    soft: 'rgba(226,232,240,0.34)',
    faint: 'rgba(226,232,240,0.10)',
    text: '#e2e8f0',
  },
}

export const OUTCOME_COLOURS = {
  goal: '#ef4444',
  shot: '#f59e0b',
  success: '#22c55e',
  fail: '#94a3b8',
  danger: '#f97316',
}

export const EVENT_GLYPHS: Record<EventKind, EventGlyphSpec> = {
  goal: { label: 'Goal', shape: 'star', radius: 3.2, strokeWidth: 0.75, opacity: 0.98, drawPriority: 100 },
  shot: { label: 'Shot', shape: 'circle', radius: 2.35, strokeWidth: 0.65, opacity: 0.86, drawPriority: 90 },
  cross: { label: 'Cross', shape: 'triangle', radius: 2.15, strokeWidth: 0.6, opacity: 0.76, drawPriority: 70 },
  pass: { label: 'Pass', shape: 'circle', radius: 1.05, strokeWidth: 0.35, opacity: 0.34, drawPriority: 30 },
  carry: { label: 'Inferred carry', shape: 'ring', radius: 1.65, strokeWidth: 0.55, opacity: 0.72, drawPriority: 52 },
  take_on: { label: 'Take on', shape: 'diamond', radius: 1.95, strokeWidth: 0.6, opacity: 0.82, drawPriority: 76 },
  tackle: { label: 'Tackle', shape: 'diamond', radius: 1.85, strokeWidth: 0.6, opacity: 0.82, drawPriority: 72 },
  interception: { label: 'Interception', shape: 'square', radius: 1.75, strokeWidth: 0.6, opacity: 0.82, drawPriority: 74 },
  recovery: { label: 'Recovery', shape: 'circle', radius: 1.55, strokeWidth: 0.5, opacity: 0.72, drawPriority: 58 },
  block: { label: 'Block', shape: 'triangle', radius: 2.05, strokeWidth: 0.65, opacity: 0.84, drawPriority: 78 },
  clearance: { label: 'Clearance', shape: 'cross', radius: 2.0, strokeWidth: 0.7, opacity: 0.82, drawPriority: 68 },
  duel: { label: 'Duel', shape: 'ring', radius: 1.75, strokeWidth: 0.55, opacity: 0.72, drawPriority: 60 },
  foul: { label: 'Foul', shape: 'cross', radius: 1.85, strokeWidth: 0.7, opacity: 0.80, drawPriority: 62 },
  set_piece: { label: 'Set piece', shape: 'square', radius: 1.95, strokeWidth: 0.65, opacity: 0.82, drawPriority: 64 },
  touch: { label: 'Touch', shape: 'circle', radius: 1.18, strokeWidth: 0.38, opacity: 0.32, drawPriority: 20 },
  other: { label: 'Other', shape: 'circle', radius: 0.95, strokeWidth: 0.3, opacity: 0.24, drawPriority: 10 },
}

export const EVENT_KIND_COLOURS: Record<EventKind, string> = {
  goal: '#ef4444',
  shot: '#f59e0b',
  pass: 'rgba(148,163,184,0.58)',
  cross: '#f97316',
  carry: '#22c55e',
  take_on: '#a78bfa',
  tackle: '#38bdf8',
  interception: '#67e8f9',
  recovery: '#2dd4bf',
  block: '#f87171',
  clearance: '#e2e8f0',
  duel: '#c084fc',
  foul: '#fb7185',
  set_piece: '#fbbf24',
  touch: 'rgba(148,163,184,0.36)',
  other: 'rgba(148,163,184,0.32)',
}

export function safeNumber(value: unknown, fallback = 0): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

export function safeText(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
}

export function safeBool(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value > 0
  const text = safeText(value).trim().toLowerCase()
  return ['true', '1', 'yes', 'y', 'successful', 'success', 'won', 'complete', 'completed', 'accurate'].includes(text)
}

export function clampPercent(value: unknown): number {
  return Math.max(0, Math.min(100, safeNumber(value)))
}

export function pitchX(value: unknown, flip = false): number {
  const x = clampPercent(value)
  const directed = flip ? 100 - x : x
  const pad = PITCH_STANDARD.fieldPadding
  return pad + (directed / 100) * (PITCH_STANDARD.viewBoxWidth - pad * 2)
}

export function pitchY(value: unknown, flip = false): number {
  const y = clampPercent(value)
  const directed = flip ? y : 100 - y
  const pad = PITCH_STANDARD.fieldPadding
  return pad + (directed / 100) * (PITCH_STANDARD.viewBoxHeight - pad * 2)
}

export function normaliseEventKind(point: PitchPoint): EventKind {
  const explicitKind = safeText(point.event_kind).trim().toLowerCase().replace(/[^a-z0-9]+/g, '')
  if (explicitKind === 'takeon' || explicitKind === 'providertakeon') return 'take_on'
  if (explicitKind === 'inferredcarry' || explicitKind === 'carry') return 'carry'
  if (safeBool(point.is_take_on) || safeBool(point.is_provider_take_on)) return 'take_on'
  if (safeBool(point.is_inferred_carry) || safeBool(point.is_carry)) return 'carry'
  const raw = `${safeText(point.event_type)} ${safeText(point.type)} ${safeText(point.label)}`.toLowerCase()
  const compact = raw.replace(/[^a-z0-9]+/g, '')
  if (safeBool(point.is_goal) || raw.includes('goal')) return 'goal'
  if (safeBool(point.is_shot) || raw.includes('shot')) return 'shot'
  if (raw.includes('cross')) return 'cross'
  if (compact.includes('takeon') || compact.includes('dribble')) return 'take_on'
  if (compact.includes('carry') || compact.includes('ballcarry') || compact.includes('run')) return 'carry'
  if (raw.includes('tackle') || raw.includes('challenge')) return 'tackle'
  if (raw.includes('interception')) return 'interception'
  if (raw.includes('recovery') || raw.includes('ball recovery')) return 'recovery'
  if (raw.includes('block')) return 'block'
  if (raw.includes('clearance')) return 'clearance'
  if (raw.includes('aerial') || raw.includes('duel')) return 'duel'
  if (raw.includes('foul')) return 'foul'
  if (raw.includes('corner') || raw.includes('free kick') || raw.includes('freekick') || raw.includes('throw')) return 'set_piece'
  if (raw.includes('pass')) return 'pass'
  if (raw.includes('touch')) return 'touch'
  return 'other'
}

export function eventColour(point: PitchPoint, tone: PitchTone = 'cyan'): string {
  const kind = normaliseEventKind(point)
  if (kind === 'goal') return OUTCOME_COLOURS.goal
  if (kind === 'shot') return OUTCOME_COLOURS.shot
  if (safeBool(point.led_to_shot)) return OUTCOME_COLOURS.danger

  const hasOutcome = point.is_success !== undefined || point.successful !== undefined
  if (hasOutcome && !safeBool(point.is_success ?? point.successful)) {
    return 'rgba(148,163,184,0.46)'
  }

  if (kind === 'other' || kind === 'touch') return TONE_COLOURS[tone].soft
  return EVENT_KIND_COLOURS[kind]
}

export function eventRadius(point: PitchPoint): number {
  const kind = normaliseEventKind(point)
  const base = EVENT_GLYPHS[kind].radius
  if (kind === 'goal') return base + 0.75
  if (kind === 'shot') return Math.max(base, Math.min(4.6, base + safeNumber(point.xg) * 4.3))
  return base
}

export function eventTooltip(point: PitchPoint): string {
  const kind = EVENT_GLYPHS[normaliseEventKind(point)].label
  const minute = safeText(point.minute) ? `${safeNumber(point.minute).toFixed(1)}' ` : ''
  const player = safeText(point.player, 'Unknown')
  const outcome = safeText(point.outcome_type)
  const xg = point.xg !== undefined && point.xg !== null && safeText(point.xg) !== '' ? ` xG ${safeNumber(point.xg).toFixed(2)}` : ''
  return `${minute}${player} ${kind}${outcome ? ` ${outcome}` : ''}${xg}`.trim()
}

export function laneLabelFromKey(key: string): string {
  return [...PITCH_LANES, ...SIMPLE_LANES].find((lane) => lane.key === key)?.label ?? key.replaceAll('_', ' ')
}
