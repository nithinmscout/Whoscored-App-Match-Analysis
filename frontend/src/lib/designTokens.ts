export const COLOUR = {
  bg: '#07090f',
  surface0: '#0d1017',
  surface1: '#111520',
  surface2: '#161c2d',
  surface3: '#1e2538',

  border: 'rgba(255,255,255,0.07)',
  borderHover: 'rgba(255,255,255,0.14)',
  borderActive: 'rgba(255,255,255,0.22)',

  accent: '#7C5CFC',
  accentSoft: 'rgba(124,92,252,0.15)',
  accentGlow: 'rgba(124,92,252,0.35)',

  signalGreen: '#22D3A0',
  signalBlue: '#60A5FA',
  signalAmber: '#F59E0B',
  signalRed: '#F87171',

  textPrimary: '#F1F5FF',
  textSecondary: '#8B92A8',
  textMuted: '#4B5268',
  textAccent: '#A78BFA',
} as const

export const TYPE = {
  display: { fontSize: 28, fontWeight: 800, letterSpacing: -0.5, lineHeight: 1.1 },
  heading1: { fontSize: 18, fontWeight: 700, letterSpacing: -0.3, lineHeight: 1.2 },
  heading2: { fontSize: 14, fontWeight: 700, letterSpacing: -0.1, lineHeight: 1.3 },
  body: { fontSize: 13, fontWeight: 400, lineHeight: 1.55 },
  small: { fontSize: 11, fontWeight: 400, lineHeight: 1.4 },
  label: { fontSize: 10, fontWeight: 700, letterSpacing: 0.9, textTransform: 'uppercase' as const },
  mono: { fontSize: 12, fontWeight: 600, fontVariantNumeric: 'tabular-nums' as const },
} as const

export const SPACE = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const

export const RADIUS = {
  sm: 6,
  md: 10,
  lg: 14,
  xl: 20,
  pill: 999,
} as const

export const SHADOW = {
  card: '0 1px 3px rgba(0,0,0,0.4), 0 4px 16px rgba(0,0,0,0.3)',
  raised: '0 4px 24px rgba(0,0,0,0.5), 0 1px 2px rgba(0,0,0,0.6)',
  glow: (colour: string) => `0 0 24px ${colour}, 0 0 8px ${colour}`,
} as const

export const TRANSITION = 'all 0.18s cubic-bezier(0.4, 0, 0.2, 1)'

export function pctColour(pct: number): string {
  if (pct >= 80) return COLOUR.signalGreen
  if (pct >= 60) return COLOUR.signalBlue
  if (pct >= 40) return COLOUR.signalAmber
  return COLOUR.signalRed
}

export function trustColour(tier?: string): string {
  if (tier === 'high') return COLOUR.signalGreen
  if (tier === 'medium') return COLOUR.signalAmber
  return COLOUR.signalRed
}