import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import AnalysisRenderProgress, { rememberAnalysisRenderDuration } from '../components/AnalysisRenderProgress'
import DataTable from '../components/DataTable'
import ProcessedStoreProgressPopup from '../components/ProcessedStoreProgressPopup'
import { EmptyPitchNote, EventLegend, PitchArrowLayer, PitchCanvas, PitchHeatLayer, PitchLaneLayer, PitchPointLayer } from '../components/EventPitch'
import { getSavedTeams, getScheduleFolders, getScheduleSeasons, getTeamEvents, getTeamSummary, rebuildTeamAnalysisProfileStore } from '../lib/api'
import type {
  TeamActionMapGroup,
  TeamCommonLineup,
  TeamHeatmap,
  TeamLaneKpiGroup,
  TeamLaneSummary,
  TeamMatchLogRow,
  TeamMetricRadarRow,
  TeamPhaseKpiBreakdown,
  TeamPhaseRadarGroup,
  TeamPhaseRadarMetric,
  TeamPitchPoint,
  TeamPlayerContribution,
  TeamPlayerInfluenceCategory,
  TeamSetPieceSection,
  TeamShapeProfile,
  TeamShotMaps,
  TeamSummaryResponse,
} from '../lib/api'
import type { TableRow } from '../types/api'

type DashboardTone = 'cyan' | 'amber' | 'violet' | 'green' | 'red' | 'neutral'
type TeamAnalysisTab = 'overview' | 'attacking' | 'defensive' | 'transitions' | 'set_pieces' | 'players' | 'season_comparison' | 'raw_events' | 'data_quality'
type TerritoryMode = 'touches' | 'final_third_entries' | 'box_entries' | 'xT' | 'xg_chain'
type ShotMode = 'shot_map' | 'shot_heatmap' | 'xg_map'
type ShapeMode = 'common' | 'in_possession' | 'defensive'
type ActionMode = 'top_xt_passes' | 'top_progressive_passes' | 'final_third_entries' | 'box_entries' | 'all_successful_actions' | 'all_unsuccessful_actions'
type LaneMode = 'touches' | 'final_third_entries' | 'box_entries' | 'xT' | 'shots' | 'xG' | 'crosses' | 'carries' | 'progressive_passes'
type SetPieceDeliveryMode = 'all' | 'shot_ending' | 'high_threat'
type FinalThirdPassMode = 'all' | 'open_play_only' | 'danger_only' | 'box_entry_only' | 'shot_chain' | 'goal_chain' | 'backwards_recycle' | 'incomplete'
type SequenceBrowserMode = 'goals' | 'big_chances' | 'highest_xg_shots' | 'highest_xt_chains'
type RoutineBrowserMode = 'best_attacking_routines' | 'worst_conceded_routines' | 'highest_xg_routines' | 'goal_routines'
type ProcessStreamEvent = Record<string, unknown>
type GenericRow = Record<string, unknown>

const TEAM_SUMMARY_MEMORY_LIMIT = 8
const TEAM_SUMMARY_MEMORY_CACHE = new Map<string, TeamSummaryResponse>()

const PIE_COLORS = [
  'rgba(45,216,233,0.92)',
  'rgba(168,85,247,0.88)',
  'rgba(34,197,94,0.86)',
  'rgba(251,191,36,0.88)',
  'rgba(248,113,113,0.86)',
  'rgba(96,165,250,0.86)',
  'rgba(244,114,182,0.84)',
  'rgba(148,163,184,0.82)',
]

const FIELD_STYLE: CSSProperties = {
  display: 'block',
  width: '100%',
  marginTop: 6,
  padding: '10px 11px',
  borderRadius: 12,
  border: '1px solid var(--border)',
  background: 'rgba(255,255,255,0.045)',
  color: 'var(--text)',
  boxSizing: 'border-box',
}

const BUTTON_STYLE: CSSProperties = {
  border: '1px solid rgba(45,216,233,0.42)',
  background: 'rgba(45,216,233,0.18)',
  color: 'var(--text)',
  borderRadius: 12,
  padding: '10px 13px',
  cursor: 'pointer',
  fontWeight: 850,
  fontSize: 12,
}

const SECONDARY_BUTTON_STYLE: CSSProperties = {
  ...BUTTON_STYLE,
  border: '1px solid rgba(255,255,255,0.16)',
  background: 'rgba(255,255,255,0.06)',
}

const TEAM_ANALYSIS_TABS: Array<{ key: TeamAnalysisTab; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'attacking', label: 'Attacking profile' },
  { key: 'defensive', label: 'Defensive profile' },
  { key: 'transitions', label: 'Transitions' },
  { key: 'set_pieces', label: 'Set pieces' },
  { key: 'players', label: 'Players' },
  { key: 'season_comparison', label: 'Season comparison' },
  { key: 'raw_events', label: 'Raw validation rows' },
  { key: 'data_quality', label: 'Data quality' },
]

const TERRITORY_OPTIONS: Array<{ key: TerritoryMode; label: string }> = [
  { key: 'touches', label: 'Touches' },
  { key: 'final_third_entries', label: 'Final third entries' },
  { key: 'box_entries', label: 'Box entries' },
  { key: 'xT', label: 'xT' },
  { key: 'xg_chain', label: 'xG chain' },
]

const ACTION_OPTIONS: Array<{ key: ActionMode; label: string }> = [
  { key: 'top_xt_passes', label: 'Top xT passes' },
  { key: 'top_progressive_passes', label: 'Top progressive passes' },
  { key: 'final_third_entries', label: 'Final third entries' },
  { key: 'box_entries', label: 'Box entries' },
  { key: 'all_successful_actions', label: 'All successful actions' },
  { key: 'all_unsuccessful_actions', label: 'All unsuccessful actions' },
]

const LANE_OPTIONS: Array<{ key: LaneMode; label: string }> = [
  { key: 'touches', label: 'Touches' },
  { key: 'final_third_entries', label: 'Final third entries' },
  { key: 'box_entries', label: 'Box entries' },
  { key: 'xT', label: 'xT' },
  { key: 'shots', label: 'Shots' },
  { key: 'xG', label: 'xG' },
  { key: 'crosses', label: 'Crosses' },
  { key: 'carries', label: 'Carries' },
  { key: 'progressive_passes', label: 'Progressive passes' },
]

const FINAL_THIRD_PASS_OPTIONS: Array<{ key: FinalThirdPassMode; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'open_play_only', label: 'Open play only' },
  { key: 'danger_only', label: 'Danger only' },
  { key: 'box_entry_only', label: 'Box entry only' },
  { key: 'shot_chain', label: 'Shot chain' },
  { key: 'goal_chain', label: 'Goal chain' },
  { key: 'backwards_recycle', label: 'Backwards recycle' },
  { key: 'incomplete', label: 'Incomplete' },
]

const SEQUENCE_BROWSER_OPTIONS: Array<{ key: SequenceBrowserMode; label: string }> = [
  { key: 'goals', label: 'Goals' },
  { key: 'big_chances', label: 'Big chances' },
  { key: 'highest_xg_shots', label: 'Highest xG shots' },
  { key: 'highest_xt_chains', label: 'Highest xT chains' },
]

const ROUTINE_BROWSER_OPTIONS: Array<{ key: RoutineBrowserMode; label: string }> = [
  { key: 'best_attacking_routines', label: 'Best attacking routines' },
  { key: 'worst_conceded_routines', label: 'Worst conceded routines' },
  { key: 'highest_xg_routines', label: 'Highest xG routines' },
  { key: 'goal_routines', label: 'Goal routines' },
]

function panelStyle(extra?: CSSProperties): CSSProperties {
  return {
    border: '1px solid rgba(255,255,255,0.08)',
    background: 'linear-gradient(180deg, rgba(17,24,39,0.96), rgba(10,15,26,0.96))',
    borderRadius: 20,
    boxShadow: '0 18px 55px rgba(0,0,0,0.24)',
    ...extra,
  }
}

function miniLabelStyle(): CSSProperties {
  return { fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: 850 }
}

function asErrorMessage(error: unknown): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError?.response?.data?.detail ?? anyError?.message ?? 'Unknown error'
}

function s(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
}

function n(value: unknown, fallback = 0): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function displayTeamName(team: string): string {
  return team.replace(/_/g, ' ')
}

function parseTierFromFolder(folder: string, nation: string): string {
  return folder.replace(nation, '').trim() || 'T1'
}

function formatValue(value: unknown, digits = 1): string {
  if (value === null || value === undefined || value === '') return 'Not available'
  const numeric = n(value, Number.NaN)
  if (Number.isFinite(numeric)) return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(digits)
  return s(value)
}

function countList<T>(items: T[] | undefined): T[] {
  return Array.isArray(items) ? items : []
}

function asRecord(value: unknown): GenericRow {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as GenericRow : {}
}

function recordList(value: unknown): GenericRow[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object').map((item) => item as GenericRow) : []
}

function recordValue(value: unknown, key: string): unknown {
  return asRecord(value)[key]
}

function toPitchRecords(points: TeamPitchPoint[] | undefined): Record<string, unknown>[] {
  return countList(points).map((point) => ({ ...point }))
}

function toLaneRecords(lanes: TeamLaneSummary[] | undefined): Record<string, unknown>[] {
  return countList(lanes).map((lane) => ({ ...lane }))
}

function Section({ title, kicker, children, right }: { title: string; kicker?: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section style={panelStyle({ padding: 20, marginBottom: 18 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          {kicker && <div style={miniLabelStyle()}>{kicker}</div>}
          <h2 style={{ margin: kicker ? '6px 0 0' : 0, fontSize: 22, lineHeight: 1.15 }}>{title}</h2>
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

function StyleTag({ label }: { label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', border: '1px solid rgba(45,216,233,0.26)', background: 'rgba(45,216,233,0.10)', color: 'var(--accent)', borderRadius: 999, padding: '6px 9px', fontSize: 11, fontWeight: 850 }}>
      {label}
    </span>
  )
}

function ToggleGroup<T extends string>({ options, value, onChange }: { options: Array<{ key: T; label: string }>; value: T; onChange: (value: T) => void }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {options.map((option) => {
        const active = option.key === value
        return (
          <button key={option.key} type="button" onClick={() => onChange(option.key)} style={active ? BUTTON_STYLE : SECONDARY_BUTTON_STYLE}>
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function TabButton({ tabKey, activeTab, onSelect, label }: { tabKey: TeamAnalysisTab; activeTab: TeamAnalysisTab; onSelect: (tab: TeamAnalysisTab) => void; label: string }) {
  const active = tabKey === activeTab
  return (
    <button
      type="button"
      onClick={() => onSelect(tabKey)}
      style={{
        ...SECONDARY_BUTTON_STYLE,
        border: active ? '1px solid rgba(45,216,233,0.56)' : '1px solid rgba(255,255,255,0.12)',
        background: active ? 'rgba(45,216,233,0.18)' : 'rgba(255,255,255,0.045)',
      }}
    >
      {label}
    </button>
  )
}

function MetricChip({ label, value, note }: { label: string; value: unknown; note?: string }) {
  return (
    <div style={panelStyle({ padding: 13, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 950, marginTop: 6 }}>{formatValue(value)}</div>
      {note && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 5, lineHeight: 1.35 }}>{note}</div>}
    </div>
  )
}

function phaseRadarScore(group: TeamPhaseRadarGroup): number {
  const directScore = n(group.score, Number.NaN)
  if (Number.isFinite(directScore)) return Math.max(0, Math.min(100, directScore))

  const scores = countList(group.metrics)
    .map((metric) => n(metric.percentile ?? metric.score, Number.NaN))
    .filter((score) => Number.isFinite(score))

  if (!scores.length) return 50
  return Math.max(0, Math.min(100, scores.reduce((total, score) => total + score, 0) / scores.length))
}

function phaseLabel(group: TeamPhaseRadarGroup): string {
  return group.title || group.key.replace(/_/g, ' ')
}

function phaseShortLabel(group: TeamPhaseRadarGroup): string {
  const label = phaseLabel(group)
  return label
    .replace('In possession', 'In poss.')
    .replace('Out of possession', 'Out poss.')
    .replace('Set pieces', 'Set pieces')
}

function polarPoint(cx: number, cy: number, radius: number, angleDegrees: number): { x: number; y: number } {
  const angle = ((angleDegrees - 90) * Math.PI) / 180
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  }
}

function pieSlicePath(cx: number, cy: number, radius: number, startAngle: number, endAngle: number): string {
  const safeEndAngle = Math.min(endAngle, startAngle + 359.99)
  const start = polarPoint(cx, cy, radius, safeEndAngle)
  const end = polarPoint(cx, cy, radius, startAngle)
  const largeArcFlag = safeEndAngle - startAngle <= 180 ? '0' : '1'
  return [
    `M ${cx} ${cy}`,
    `L ${start.x.toFixed(3)} ${start.y.toFixed(3)}`,
    `A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x.toFixed(3)} ${end.y.toFixed(3)}`,
    'Z',
  ].join(' ')
}

function PhaseRadarOverview({ groups, selectedKey, onSelect }: { groups: TeamPhaseRadarGroup[] | undefined; selectedKey: string; onSelect: (key: string) => void }) {
  const rows = countList(groups)
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No phase radar groups available.</div>

  const size = 336
  const centre = size / 2
  const radius = 112
  const axes = rows.map((group, index) => {
    const angle = ((Math.PI * 2 * index) / Math.max(rows.length, 1)) - Math.PI / 2
    const score = phaseRadarScore(group)
    const pct = score / 100
    return {
      group,
      angle,
      score,
      x: centre + Math.cos(angle) * radius * pct,
      y: centre + Math.sin(angle) * radius * pct,
      endX: centre + Math.cos(angle) * radius,
      endY: centre + Math.sin(angle) * radius,
      labelX: centre + Math.cos(angle) * (radius + 34),
      labelY: centre + Math.sin(angle) * (radius + 34),
    }
  })
  const polygon = axes.map((axis) => `${axis.x},${axis.y}`).join(' ')

  return (
    <div style={panelStyle({ padding: 18, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>All phases</div>
      <h3 style={{ margin: '6px 0 4px', fontSize: 19 }}>Single phase identity radar</h3>
      <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, marginBottom: 12 }}>
        Each axis shows the normalised strength of one phase. Pick a phase below to update the KPI pie on the right.
      </div>

      <svg width="100%" viewBox={`0 0 ${size} ${size}`} style={{ display: 'block', maxHeight: 380 }}>
        {[0.25, 0.5, 0.75, 1].map((ring) => (
          <circle key={ring} cx={centre} cy={centre} r={radius * ring} fill="none" stroke="rgba(255,255,255,0.075)" strokeWidth="1" />
        ))}
        {axes.map((axis) => (
          <line key={axis.group.key} x1={centre} y1={centre} x2={axis.endX} y2={axis.endY} stroke="rgba(255,255,255,0.09)" strokeWidth="1" />
        ))}
        <polygon points={polygon} fill="rgba(45,216,233,0.22)" stroke="rgba(45,216,233,0.94)" strokeWidth="2.4" />
        {axes.map((axis) => {
          const active = axis.group.key === selectedKey
          return (
            <g key={`${axis.group.key}-label`}>
              <circle cx={axis.x} cy={axis.y} r={active ? 5.5 : 4} fill={active ? 'rgba(251,191,36,0.96)' : 'rgba(45,216,233,0.96)'} stroke="rgba(8,13,24,0.95)" strokeWidth="2" />
              <text
                x={axis.labelX}
                y={axis.labelY}
                textAnchor={axis.labelX < centre - 10 ? 'end' : axis.labelX > centre + 10 ? 'start' : 'middle'}
                dominantBaseline="middle"
                fill={active ? 'rgba(251,191,36,0.98)' : 'rgba(226,232,240,0.90)'}
                fontSize="10"
                fontWeight="800"
              >
                {phaseShortLabel(axis.group)}
              </text>
            </g>
          )
        })}
      </svg>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(142px, 1fr))', gap: 9, marginTop: 12 }}>
        {axes.map((axis) => {
          const active = axis.group.key === selectedKey
          return (
            <button
              key={axis.group.key}
              type="button"
              onClick={() => onSelect(axis.group.key)}
              style={{
                ...panelStyle({ padding: 10, boxShadow: 'none', background: active ? 'rgba(45,216,233,0.12)' : 'rgba(255,255,255,0.035)' }),
                cursor: 'pointer',
                color: 'var(--text)',
                textAlign: 'left',
                border: active ? '1px solid rgba(45,216,233,0.40)' : '1px solid rgba(255,255,255,0.08)',
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 950 }}>{phaseLabel(axis.group)}</div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 5 }}>{axis.group.strength ?? 'Profile'}</div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function PhaseKpiPieChart({ items }: { items: TeamPhaseRadarMetric[] }) {
  const cleaned = items
    .map((item) => ({
      item,
      score: Math.max(0.1, Math.min(100, n(item.percentile ?? item.score, 0))),
    }))
    .filter((entry) => entry.score > 0)

  if (!cleaned.length) {
    return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No KPI values available for this phase.</div>
  }

  const total = cleaned.reduce((sum, entry) => sum + entry.score, 0)
  let cursor = 0
  const size = 236
  const centre = size / 2
  const radius = 98
  const strongest = cleaned.reduce((best, entry) => (entry.score > best.score ? entry : best), cleaned[0])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 14, alignItems: 'center' }}>
      <svg width="100%" viewBox={`0 0 ${size} ${size}`} style={{ display: 'block', maxHeight: 300 }}>
        <circle cx={centre} cy={centre} r={radius} fill="rgba(255,255,255,0.045)" />
        {cleaned.map((entry, index) => {
          const startAngle = cursor
          const sweep = (entry.score / total) * 360
          const endAngle = cursor + sweep
          cursor = endAngle
          return (
            <path
              key={`${s(entry.item.label, 'KPI')}-${index}`}
              d={pieSlicePath(centre, centre, radius, startAngle, endAngle)}
              fill={PIE_COLORS[index % PIE_COLORS.length]}
              stroke="rgba(8,13,24,0.95)"
              strokeWidth="2"
            />
          )
        })}
        <circle cx={centre} cy={centre} r="48" fill="rgba(8,13,24,0.94)" stroke="rgba(255,255,255,0.10)" strokeWidth="1" />
        <text x={centre} y={centre - 5} textAnchor="middle" fill="rgba(226,232,240,0.96)" fontSize="12" fontWeight="900">KPI mix</text>
        <text x={centre} y={centre + 13} textAnchor="middle" fill="rgba(148,163,184,0.92)" fontSize="10">normalised</text>
      </svg>

      <div style={{ display: 'grid', gap: 9 }}>
        {cleaned.map((entry, index) => {
          const share = total > 0 ? (entry.score / total) * 100 : 0
          return (
            <div key={`${s(entry.item.label, 'KPI')}-legend-${index}`} style={{ display: 'grid', gridTemplateColumns: '12px 1fr auto', gap: 9, alignItems: 'center' }}>
              <span style={{ width: 10, height: 10, borderRadius: 999, background: PIE_COLORS[index % PIE_COLORS.length], display: 'inline-block' }} />
              <div>
                <div style={{ fontSize: 12, fontWeight: 900 }}>{s(entry.item.label, 'KPI')}</div>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 2 }}>{s(entry.item.strength || entry.item.note, 'Profile indicator')}</div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 850 }}>{share.toFixed(0)}%</div>
            </div>
          )
        })}
        {strongest && (
          <div style={{ marginTop: 4, color: 'var(--muted)', fontSize: 11, lineHeight: 1.4 }}>
            Strongest slice: <span style={{ color: 'var(--text)', fontWeight: 900 }}>{s(strongest.item.label, 'KPI')}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function PhaseBreakdownPanel({ breakdown }: { breakdown?: TeamPhaseKpiBreakdown }) {
  if (!breakdown) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Select a phase to see its KPI breakdown.</div>
  const items = countList(breakdown.items)

  return (
    <div style={panelStyle({ padding: 18, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>Selected phase KPI pie</div>
      <h3 style={{ margin: '6px 0 8px', fontSize: 19 }}>{breakdown.title ?? 'Phase breakdown'}</h3>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14, lineHeight: 1.45 }}>
        {breakdown.strength ?? 'Profile'} phase split by its underlying KPIs, so the right side explains what is driving the radar score.
      </div>
      <PhaseKpiPieChart items={items} />
    </div>
  )
}

function LeagueMetricStrip({ rows }: { rows: TeamMetricRadarRow[] | undefined }) {
  const metrics = countList(rows).slice(0, 18)
  if (!metrics.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>League metric comparison is unavailable for this selection.</div>

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {metrics.map((row) => {
        const pct = Math.max(0, Math.min(100, n(row.percentile, 50)))
        const level = pct >= 72 ? 'Strong' : pct >= 48 ? 'Average' : 'Weak'
        return (
          <div key={row.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 1fr) 2fr auto', gap: 12, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 900 }}>{row.label}</div>
              <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3 }}>{row.category}</div>
            </div>
            <div style={{ height: 9, borderRadius: 999, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
              <div style={{ width: `${pct}%`, height: '100%', background: 'rgba(45,216,233,0.75)', borderRadius: 999 }} />
            </div>
            <div style={{ minWidth: 88, textAlign: 'right' }}>
              <span style={{ display: 'inline-flex', padding: '5px 8px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.05)', fontSize: 11, fontWeight: 900 }}>{level}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function GoalDistanceRings() {
  return (
    <g pointerEvents="none">
      {[12, 24, 36, 48].map((radius) => (
        <circle key={radius} cx="105" cy="34" r={radius} fill="none" stroke="rgba(255,255,255,0.11)" strokeWidth="0.55" strokeDasharray="2 2" />
      ))}
    </g>
  )
}

function PitchPanel({
  title,
  note,
  points,
  arrows,
  lanes,
  heatmap,
  tone = 'cyan',
  height = 315,
  showGoalRings = false,
  legend = ['pass', 'cross', 'carry', 'shot', 'goal', 'tackle', 'interception'],
}: {
  title: string
  note?: string
  points?: TeamPitchPoint[]
  arrows?: TeamPitchPoint[]
  lanes?: TeamLaneSummary[]
  heatmap?: TeamHeatmap
  tone?: DashboardTone
  height?: number
  showGoalRings?: boolean
  legend?: string[]
}) {
  const pitchPoints = toPitchRecords(points)
  const arrowRecords = toPitchRecords(arrows)
  const laneRecords = toLaneRecords(lanes)
  const heatmapRecord = heatmap ? { ...heatmap } : null
  const hasData = pitchPoints.length > 0 || arrowRecords.length > 0 || laneRecords.length > 0 || Boolean(heatmapRecord?.cells?.length)

  return (
    <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 950 }}>{title}</div>
          {note && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, lineHeight: 1.35 }}>{note}</div>}
        </div>
        <EventLegend items={legend} tone={tone} />
      </div>
      <div style={{ marginTop: 10 }}>
        <PitchCanvas height={height} showDirection>
          {showGoalRings && <GoalDistanceRings />}
          {heatmapRecord?.cells?.length ? <PitchHeatLayer heatmap={heatmapRecord as Record<string, unknown>} tone={tone} /> : null}
          {laneRecords.length ? <PitchLaneLayer lanes={laneRecords} tone={tone} /> : null}
          {arrowRecords.length ? <PitchArrowLayer arrows={arrowRecords} tone={tone} maxArrows={220} /> : null}
          {pitchPoints.length ? <PitchPointLayer points={pitchPoints} tone={tone} maxPoints={260} /> : null}
          {!hasData && <EmptyPitchNote label="No matching events available." />}
        </PitchCanvas>
      </div>
    </div>
  )
}

function CommonLineupPitch({ summary }: { summary: TeamSummaryResponse }) {
  const [mode, setMode] = useState<ShapeMode>('common')
  const selectedShape: TeamCommonLineup | TeamShapeProfile | undefined = mode === 'in_possession'
    ? summary.in_possession_shape
    : mode === 'defensive'
      ? summary.defensive_shape
      : summary.common_lineup
  const players = countList(selectedShape?.players)

  return (
    <div style={panelStyle({ padding: 16, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div>
          <div style={miniLabelStyle()}>Season shape</div>
          <h3 style={{ margin: '6px 0 4px', fontSize: 18 }}>{mode === 'common' ? 'Common lineup' : mode === 'in_possession' ? 'In possession positions' : 'Defensive positions'}</h3>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.4 }}>
            Formation estimate {selectedShape?.formation_guess ?? 'Unknown'}. {selectedShape?.method ?? selectedShape?.note}
          </div>
        </div>
        <ToggleGroup
          value={mode}
          onChange={setMode}
          options={[
            { key: 'common', label: 'Common lineup' },
            { key: 'in_possession', label: 'In possession positions' },
            { key: 'defensive', label: 'Defensive positions' },
          ]}
        />
      </div>
      <div style={{ marginTop: 12 }}>
        <PitchCanvas height={410} showDirection>
          {players.map((player, index) => {
            const x = (Math.max(5, Math.min(95, n(player.pitch_x, 50))) / 100) * 105
            const y = (Math.max(7, Math.min(93, n(player.pitch_y, 50))) / 100) * 68
            return (
              <g key={`${player.player}-${index}`}>
                <circle cx={x} cy={y} r="4.8" fill="rgba(45,216,233,0.82)" stroke="rgba(255,255,255,0.72)" strokeWidth="0.7" />
                <text x={x} y={y + 1.2} textAnchor="middle" fill="rgba(2,6,23,0.95)" fontSize="3.6" fontWeight="950">{s(player.shirt_no, String(index + 1))}</text>
                <text x={x} y={y + 8.8} textAnchor="middle" fill="rgba(232,234,240,0.92)" fontSize="3.2" fontWeight="900">{s(player.player, 'Unknown').split(' ').slice(-1)[0]}</text>
                <title>{`${s(player.player, 'Unknown')} ${s(player.position_label, '')}. Events ${formatValue(player.events)}.`}</title>
              </g>
            )
          })}
          {!players.length && <EmptyPitchNote label="No common lineup estimate available." />}
        </PitchCanvas>
      </div>
      {selectedShape?.note && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45, marginTop: 10 }}>{selectedShape.note}</div>}
    </div>
  )
}

function PlayerMiniList({ title, players, metric, why }: { title: string; players: TeamPlayerContribution[] | undefined; metric: keyof TeamPlayerContribution | string; why?: string }) {
  const rows = countList(players).slice(0, 6)
  return (
    <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>{title}</div>
      {why && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.35, marginTop: 6 }}>{why}</div>}
      <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
        {rows.length ? rows.map((player, index) => {
          const row = player as Record<string, unknown>
          const mainMetric = s(row.main_metric, s(metric))
          const mainValue = row.main_metric_value ?? row[metric]
          const secondaryMetric = s(row.secondary_metric)
          const secondaryValue = row.secondary_metric_value
          return (
            <div key={`${player.player}-${index}`} style={{ display: 'grid', gridTemplateColumns: '26px 1fr auto', gap: 9, alignItems: 'start' }}>
              <div style={{ width: 26, height: 26, borderRadius: 10, display: 'grid', placeItems: 'center', background: 'rgba(45,216,233,0.12)', color: 'var(--accent)', fontSize: 11, fontWeight: 950 }}>{index + 1}</div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12, fontWeight: 850, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{player.player ?? 'Unknown'}</div>
                <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 2 }}>
                  {mainMetric}{secondaryMetric ? `, ${secondaryMetric} ${formatValue(secondaryValue)}` : ''}
                </div>
                {row.why_he_appears && <div style={{ color: 'var(--muted)', fontSize: 10, lineHeight: 1.35, marginTop: 4 }}>{s(row.why_he_appears)}</div>}
                {row.matches_involved && <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 3 }}>Matches involved: {formatValue(row.matches_involved, 0)}</div>}
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>{formatValue(mainValue)}</div>
            </div>
          )
        }) : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No player data available.</div>}
      </div>
    </div>
  )
}

function ShotDashboard({ shotMaps }: { shotMaps?: TeamShotMaps }) {
  const [mode, setMode] = useState<ShotMode>('shot_map')
  const points = mode === 'xg_map' ? shotMaps?.xg_map?.points : shotMaps?.shot_map ?? shotMaps?.points
  const heatmap = mode === 'shot_heatmap' ? shotMaps?.shot_heatmap : mode === 'xg_map' ? shotMaps?.xg_map?.heatmap : undefined
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup
        value={mode}
        onChange={setMode}
        options={[
          { key: 'shot_map', label: 'Shot map' },
          { key: 'shot_heatmap', label: 'Shot heatmap' },
          { key: 'xg_map', label: 'xG map' },
        ]}
      />
      <PitchPanel
        title={mode === 'shot_heatmap' ? 'Shot heatmap' : mode === 'xg_map' ? 'xG map' : 'Shot map'}
        note="Bigger pitch view with subtle distance rings from the centre of the goal. Marker size follows xG where available."
        points={mode === 'shot_heatmap' ? undefined : points}
        heatmap={heatmap}
        showGoalRings
        height={440}
        tone="amber"
        legend={['shot', 'goal']}
      />
    </div>
  )
}

function TerritoryDashboard({ territory }: { territory?: Record<string, TeamHeatmap | undefined> }) {
  const [mode, setMode] = useState<TerritoryMode>('touches')
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={setMode} options={TERRITORY_OPTIONS} />
      <PitchPanel title="Attacking territory" note="Colour intensity shows territory without crowding the pitch with raw labels." heatmap={territory?.[mode]} height={390} tone="cyan" legend={['touch', 'pass', 'carry', 'shot']} />
    </div>
  )
}

function ActionMapDashboard({ title, maps, carry = false }: { title: string; maps?: TeamActionMapGroup; carry?: boolean }) {
  const [mode, setMode] = useState<ActionMode>('top_xt_passes')
  const arrows = countList(maps?.[mode])
  const options = carry
    ? ACTION_OPTIONS.map((option) => ({ ...option, label: option.label.replace('passes', 'carries').replace('pass', 'carry') }))
    : ACTION_OPTIONS
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={setMode} options={options} />
      <PitchPanel
        title={title}
        note={carry ? 'Carries are shown with dotted directional arrows from start to end.' : 'Passes are shown with solid directional arrows from start to end.'}
        arrows={arrows}
        height={390}
        tone={carry ? 'violet' : 'cyan'}
        legend={carry ? ['carry'] : ['pass', 'cross']}
      />
    </div>
  )
}

function LaneStatsDashboard({ laneKpis }: { laneKpis?: TeamLaneKpiGroup }) {
  const [mode, setMode] = useState<LaneMode>('touches')
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={setMode} options={LANE_OPTIONS} />
      <PitchPanel title="Lane profile" note="Lane intensity is shown on the pitch. Raw labels are kept minimal to make the picture easier to read." lanes={laneKpis?.[mode]} height={340} tone="violet" legend={['pass', 'carry', 'shot']} />
    </div>
  )
}

function deliveryLinesForMode(section: TeamSetPieceSection | undefined, mode: SetPieceDeliveryMode, side: 'for' | 'against'): TeamPitchPoint[] {
  const block = side === 'for' ? section?.for : section?.against
  if (mode === 'shot_ending') return countList(block?.shot_ending_deliveries)
  if (mode === 'high_threat') return countList(block?.high_threat_deliveries)
  return countList(block?.delivery_lines ?? block?.delivery_locations)
}

function SetPieceSectionCard({ title, section }: { title: string; section: TeamSetPieceSection | undefined }) {
  const [mode, setMode] = useState<SetPieceDeliveryMode>('all')
  const forLines = deliveryLinesForMode(section, mode, 'for')
  const againstLines = deliveryLinesForMode(section, mode, 'against')
  const attackingRoutineRows = recordList(recordValue(section?.for, 'routine_groups')).slice(0, 5)
  const concededRoutineRows = recordList(recordValue(section?.against, 'routine_groups')).slice(0, 5)
  return (
    <div style={panelStyle({ padding: 16, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={miniLabelStyle()}>{title}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 5 }}>
            Delivery lines are coloured by threat caused. Low value lines stay subtle and high threat lines stand out.
          </div>
        </div>
        <ToggleGroup
          value={mode}
          onChange={setMode}
          options={[
            { key: 'all', label: 'All deliveries' },
            { key: 'shot_ending', label: 'Shot ending deliveries' },
            { key: 'high_threat', label: 'High threat deliveries' },
          ]}
        />
      </div>
      <div className="team-analysis-visual-grid">
        <PitchPanel title={`${title} for`} note="Start to end delivery map" arrows={forLines} points={section?.for?.shot_locations} height={360} tone="amber" legend={['set_piece', 'cross', 'shot', 'goal']} />
        <PitchPanel title={`${title} conceded`} note="Opponent delivery and shot threat conceded" arrows={againstLines} points={section?.against?.shot_locations} height={360} tone="red" legend={['set_piece', 'cross', 'shot', 'goal']} />
      </div>
      <div className="team-analysis-visual-grid" style={{ marginTop: 12 }}>
        <PlayerMiniList title="Main takers" players={section?.for?.main_takers} metric="set_piece_involvement" why="Ranked by set piece involvement across the selected season." />
        <PlayerMiniList title="Main targets" players={section?.for?.main_targets} metric="shots" why="Ranked by shot involvement and target value from these situations." />
      </div>
      <div className="team-analysis-visual-grid" style={{ marginTop: 12 }}>
        <SimpleRows title="Attacking routine groups" rows={attackingRoutineRows} columns={[['routine', 'Routine'], ['count_used', 'Used'], ['shots_created', 'Shots'], ['goals_created', 'Goals'], ['xg_created', 'xG'], ['shot_rate_pct', 'Shot rate']]} />
        <SimpleRows title="Conceded routine groups" rows={concededRoutineRows} columns={[['routine', 'Routine'], ['count_used', 'Used'], ['shots_conceded', 'Shots conceded'], ['goals_conceded', 'Goals conceded'], ['xg_conceded', 'xG conceded']]} />
      </div>
    </div>
  )
}

function PlayerContributionTable({ players }: { players: TeamPlayerContribution[] | undefined }) {
  const rows = countList(players)
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No player contribution table available.</div>
  const columns: Array<{ key: keyof TeamPlayerContribution; label: string }> = [
    { key: 'player', label: 'Player' },
    { key: 'events', label: 'Events' },
    { key: 'shots', label: 'Shots' },
    { key: 'goals', label: 'Goals' },
    { key: 'xg', label: 'xG' },
    { key: 'xa', label: 'xA' },
    { key: 'xt', label: 'xT' },
    { key: 'final_third_entries', label: 'F3 entries' },
    { key: 'box_entries', label: 'Box entries' },
    { key: 'defensive_actions', label: 'Def actions' },
    { key: 'high_regains', label: 'High regains' },
    { key: 'set_piece_involvement', label: 'Set pieces' },
  ]

  return (
    <div className="scroll-table" style={{ maxHeight: 520 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={String(column.key)} style={{ position: 'sticky', top: 0, background: '#111827', color: 'var(--muted)', textAlign: 'left', padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{column.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.player}-${index}`}>
              {columns.map((column) => (
                <td key={String(column.key)} style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{formatValue(row[column.key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PlayerInfluenceDashboard({ categories }: { categories?: TeamPlayerInfluenceCategory[] }) {
  const rows = countList(categories)
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No player influence dashboard available.</div>
  return (
    <div className="team-analysis-visual-grid">
      {rows.map((category) => (
        <PlayerMiniList key={category.key ?? category.title} title={s(category.title, 'Player group')} players={category.players} metric={category.metric ?? 'events'} why={category.why} />
      ))}
    </div>
  )
}

function MatchLog({ rows }: { rows: TeamMatchLogRow[] | undefined }) {
  const matches = countList(rows).slice(0, 12)
  if (!matches.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No match log available.</div>
  return (
    <div className="scroll-table" style={{ maxHeight: 360 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            {['Date', 'Opponent', 'H or A', 'Score', 'Shots', 'Box entries', 'F3 entries', 'Def actions'].map((heading) => (
              <th key={heading} style={{ textAlign: 'left', padding: '9px 10px', borderBottom: '1px solid var(--border)', color: 'var(--muted)' }}>{heading}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matches.map((row) => (
            <tr key={String(row.match_id)}>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.date}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.opponent || 'Unknown'}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.home_away}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.score}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.shots}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.box_entries}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.final_third_entries}</td>
              <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{row.defensive_actions}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SeasonComparisonTable({ rows }: { rows: NonNullable<TeamSummaryResponse['multi_season_profile']>['rows'] }) {
  const seasons = countList(rows)
  if (!seasons.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>No season comparison available.</div>
  const columns = ['season', 'matches_covered', 'goals_for', 'goals_against', 'shots_for', 'shots_against', 'xg_for', 'xg_against', 'xa', 'xt', 'high_regains'] as const
  return (
    <div className="scroll-table" style={{ maxHeight: 420 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>{columns.map((column) => <th key={column} style={{ textAlign: 'left', padding: '9px 10px', borderBottom: '1px solid var(--border)', color: 'var(--muted)' }}>{column.replace(/_/g, ' ')}</th>)}</tr>
        </thead>
        <tbody>
          {seasons.map((row, index) => (
            <tr key={`${row.season}-${index}`}>{columns.map((column) => <td key={column} style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{formatValue(row[column])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function CompactWarning({ text }: { text?: unknown }) {
  const message = s(text)
  if (!message) return null
  return <div style={{ color: '#fbbf24', fontSize: 12, lineHeight: 1.45, marginBottom: 12 }}>{message}</div>
}

function MiniBar({ value, max = 100, tone = 'cyan' }: { value: unknown; max?: number; tone?: DashboardTone }) {
  const pct = Math.max(0, Math.min(100, (n(value) / Math.max(max, 1)) * 100))
  const fill = tone === 'red' ? 'rgba(248,113,113,0.78)' : tone === 'amber' ? 'rgba(251,191,36,0.80)' : tone === 'green' ? 'rgba(34,197,94,0.76)' : 'rgba(45,216,233,0.76)'
  return (
    <div style={{ height: 9, borderRadius: 999, background: 'rgba(255,255,255,0.075)', overflow: 'hidden' }}>
      <div style={{ width: `${pct}%`, height: '100%', borderRadius: 999, background: fill }} />
    </div>
  )
}

function SeasonalSummaryPanel({ panel }: { panel: unknown }) {
  const rows = recordList(recordValue(panel, 'rows'))
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Seasonal comparison panel is unavailable.</div>
  const maxValue = Math.max(...rows.map((row) => n(row.value)), 1)
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {rows.map((row) => {
        const higherIsBetter = row.higher_is_better !== false
        const percentile = n(row.percentile, 50)
        const tone: DashboardTone = higherIsBetter ? 'cyan' : 'red'
        return (
          <div key={s(row.key)} style={{ display: 'grid', gridTemplateColumns: 'minmax(170px, 1.15fr) minmax(170px, 2fr) minmax(160px, 1.05fr)', gap: 12, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 950 }}>{s(row.label)}</div>
              <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 3 }}>
                {s(row.rank_text) || `Percentile ${formatValue(percentile, 0)}`}
              </div>
            </div>
            <div style={{ display: 'grid', gap: 5 }}>
              <MiniBar value={row.value} max={maxValue} tone={tone} />
              <div style={{ color: 'var(--muted)', fontSize: 10 }}>League average {formatValue(row.league_average)}. Previous {formatValue(row.previous_value)}.</div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center' }}>
              <span style={{ fontSize: 17, fontWeight: 950 }}>{formatValue(row.value)}</span>
              <span style={{ fontSize: 11, color: 'var(--muted)', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 999, padding: '4px 7px' }}>{s(row.trend, 'flat')}</span>
            </div>
          </div>
        )
      })}
      {s(recordValue(panel, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(panel, 'note'))}</div>}
    </div>
  )
}

function SeasonalRadarComparison({ radar }: { radar: unknown }) {
  const axes = recordList(recordValue(radar, 'axes')).slice(0, 10)
  if (!axes.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Seasonal radar comparison is unavailable.</div>
  const size = 330
  const centre = size / 2
  const radius = 112
  const pointFor = (score: unknown, index: number) => {
    const angle = ((Math.PI * 2 * index) / Math.max(axes.length, 1)) - Math.PI / 2
    const pct = Math.max(0, Math.min(100, n(score, 50))) / 100
    return { x: centre + Math.cos(angle) * radius * pct, y: centre + Math.sin(angle) * radius * pct, endX: centre + Math.cos(angle) * radius, endY: centre + Math.sin(angle) * radius, angle }
  }
  const polygon = (key: string, fallback: number) => axes.map((axis, index) => {
    const point = pointFor(axis[key] ?? fallback, index)
    return `${point.x},${point.y}`
  }).join(' ')
  return (
    <div style={panelStyle({ padding: 16, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>Season radar</div>
      <h3 style={{ margin: '6px 0 4px', fontSize: 18 }}>Current season against league and previous season</h3>
      <svg width="100%" viewBox={`0 0 ${size} ${size}`} style={{ display: 'block', maxHeight: 360 }}>
        {[0.25, 0.5, 0.75, 1].map((ring) => <circle key={ring} cx={centre} cy={centre} r={radius * ring} fill="none" stroke="rgba(255,255,255,0.075)" />)}
        {axes.map((axis, index) => {
          const point = pointFor(100, index)
          const labelPoint = pointFor(122, index)
          return (
            <g key={s(axis.key, String(index))}>
              <line x1={centre} y1={centre} x2={point.endX} y2={point.endY} stroke="rgba(255,255,255,0.08)" />
              <text x={labelPoint.x} y={labelPoint.y} textAnchor={labelPoint.x < centre ? 'end' : labelPoint.x > centre ? 'start' : 'middle'} dominantBaseline="middle" fill="rgba(226,232,240,0.82)" fontSize="9" fontWeight="800">{s(axis.label).replace(' per match', '')}</text>
            </g>
          )
        })}
        <polygon points={polygon('league_average_score', 50)} fill="rgba(148,163,184,0.08)" stroke="rgba(148,163,184,0.55)" strokeWidth="1.6" />
        {axes.some((axis) => axis.previous_score !== null && axis.previous_score !== undefined) && <polygon points={polygon('previous_score', 50)} fill="rgba(251,191,36,0.08)" stroke="rgba(251,191,36,0.70)" strokeWidth="1.8" />}
        <polygon points={polygon('current_score', 50)} fill="rgba(45,216,233,0.18)" stroke="rgba(45,216,233,0.96)" strokeWidth="2.3" />
      </svg>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 11 }}>
        <span>Current season</span><span>League average baseline</span><span>Previous season where available</span>
      </div>
      {s(recordValue(radar, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45, marginTop: 8 }}>{s(recordValue(radar, 'note'))}</div>}
    </div>
  )
}

function SeasonPhaseVerdicts({ verdicts }: { verdicts: unknown }) {
  const items = recordList(recordValue(verdicts, 'items'))
  if (!items.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Season phase verdicts are unavailable.</div>
  return (
    <div className="team-analysis-visual-grid">
      {items.map((item) => (
        <div key={s(item.phase)} style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
          <div style={miniLabelStyle()}>{s(item.phase)}</div>
          <div style={{ marginTop: 8, display: 'grid', gap: 7, fontSize: 12, lineHeight: 1.45 }}>
            <div><strong>Strongest route:</strong> {s(item.strongest_route)}</div>
            <div><strong>Main risk:</strong> {s(item.main_risk)}</div>
            <div><strong>Repeatability:</strong> {s(item.repeatability)}</div>
            <div><strong>League context:</strong> {s(item.league_context)}</div>
            <div><strong>Video check:</strong> {s(item.video_check)}</div>
          </div>
        </div>
      ))}
      {s(recordValue(verdicts, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(verdicts, 'note'))}</div>}
    </div>
  )
}

function StyleEvidencePanel({ panel }: { panel: unknown }) {
  const rows = recordList(recordValue(panel, 'rows'))
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Style evidence is unavailable.</div>
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
      {rows.map((row) => {
        const metrics = recordList(row.evidence_metrics)
        return (
          <div key={s(row.tag)} style={panelStyle({ padding: 14, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
            <div style={{ fontSize: 14, fontWeight: 950 }}>{s(row.tag)}</div>
            <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
              {metrics.map((metric, index) => (
                <div key={`${s(row.tag)}-${index}`} style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, fontSize: 11 }}>
                  <span style={{ color: 'var(--muted)' }}>{s(metric.metric)}</span>
                  <strong>{formatValue(metric.value)} {s(metric.rank_text)}</strong>
                </div>
              ))}
            </div>
            <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.4, marginTop: 10 }}>{s(row.video_check_note)}</div>
          </div>
        )
      })}
    </div>
  )
}

function SeasonalMomentumPanel({ momentum }: { momentum: unknown }) {
  const data = asRecord(momentum)
  if (data.available === false) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.note, 'Seasonal momentum is unavailable.')}</div>
  const rows = recordList(data.match_by_match)
  const intervals = recordList(data.interval_momentum)
  const recent = asRecord(data.recent_five)
  const previous = asRecord(data.previous_five)
  const maxDanger = Math.max(...rows.map((row) => Math.max(n(row.team_danger), n(row.opponent_danger))), 1)
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="team-analysis-visual-grid">
        <MetricChip label="Recent five attack" value={recent.attacking_momentum} />
        <MetricChip label="Recent five exposure" value={recent.defensive_exposure} />
        <MetricChip label="Previous five attack" value={previous.attacking_momentum} />
        <MetricChip label="Previous five exposure" value={previous.defensive_exposure} />
      </div>
      <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
        <div style={miniLabelStyle()}>Match by match danger trend</div>
        <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
          {rows.slice(-12).map((row) => (
            <div key={s(row.match_id)} style={{ display: 'grid', gridTemplateColumns: '70px 1fr 1fr 54px', gap: 8, alignItems: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>M{formatValue(row.match_number, 0)}</div>
              <MiniBar value={row.team_danger} max={maxDanger} tone="cyan" />
              <MiniBar value={row.opponent_danger} max={maxDanger} tone="red" />
              <div style={{ color: 'var(--muted)', fontSize: 11, textAlign: 'right' }}>{formatValue(row.net_danger)}</div>
            </div>
          ))}
        </div>
      </div>
      <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
        <div style={miniLabelStyle()}>Fifteen minute interval momentum</div>
        <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
          {intervals.slice(0, 8).map((row) => (
            <div key={s(row.interval_label)} style={{ display: 'grid', gridTemplateColumns: '90px 1fr auto', gap: 8, alignItems: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>{s(row.interval_label)}</div>
              <MiniBar value={Math.abs(n(row.net))} max={Math.max(...intervals.map((item) => Math.abs(n(item.net))), 1)} tone={n(row.net) >= 0 ? 'cyan' : 'red'} />
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>{formatValue(row.net)}</div>
            </div>
          ))}
        </div>
      </div>
      {s(data.note) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(data.note)}</div>}
    </div>
  )
}

function GoalkeeperDistributionPanel({ distribution }: { distribution: unknown }) {
  const data = asRecord(distribution)
  if (data.available === false) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.note)}</div>
  const summary = asRecord(data.summary)
  const targetZones = recordList(data.target_zones)
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="team-analysis-visual-grid">
        <MetricChip label="Short distribution" value={summary.short_distribution} />
        <MetricChip label="Medium distribution" value={summary.medium_distribution} />
        <MetricChip label="Long distribution" value={summary.long_distribution} />
        <MetricChip label="Launched passes" value={summary.launched_passes} />
        <MetricChip label="Completion" value={summary.completion_pct} note="Percent" />
        <MetricChip label="Progressive distribution" value={summary.progressive_distribution} />
      </div>
      <div className="team-analysis-visual-grid">
        <PitchPanel title="Goalkeeper pass arrows" arrows={data.pass_arrows as TeamPitchPoint[]} height={370} tone="cyan" legend={['pass']} note="Only shown when goalkeeper rows are safely inferred." />
        <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
          <div style={miniLabelStyle()}>Target zones</div>
          <div style={{ display: 'grid', gap: 9, marginTop: 12 }}>
            {targetZones.map((row) => (
              <div key={s(row.zone)} style={{ display: 'grid', gridTemplateColumns: '90px 1fr auto', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 12 }}>{s(row.label)}</span>
                <MiniBar value={row.passes} max={Math.max(...targetZones.map((item) => n(item.passes)), 1)} />
                <span style={{ color: 'var(--muted)', fontSize: 11 }}>{formatValue(row.completion_pct, 0)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      {s(data.note) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(data.note)}</div>}
    </div>
  )
}

function FinalThirdPassClassificationPanel({ classification }: { classification: unknown }) {
  const [mode, setMode] = useState<FinalThirdPassMode>('all')
  const maps = asRecord(recordValue(classification, 'maps'))
  const counts = asRecord(recordValue(classification, 'counts'))
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={setMode} options={FINAL_THIRD_PASS_OPTIONS} />
      <PitchPanel title="Final third pass classification" note={`Selected filter count: ${formatValue(counts[mode], 0)}`} arrows={(maps[mode] as TeamPitchPoint[]) ?? []} height={395} tone="cyan" legend={['pass', 'cross']} />
      {s(recordValue(classification, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(classification, 'note'))}</div>}
    </div>
  )
}

function RepeatedPossessionChainsPanel({ chains }: { chains: unknown }) {
  const families = recordList(recordValue(chains, 'families'))
  const examples = recordList(recordValue(chains, 'examples'))
  const [selected, setSelected] = useState(0)
  const example = examples[Math.min(selected, Math.max(examples.length - 1, 0))]
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="team-analysis-visual-grid">
        {families.slice(0, 6).map((row, index) => (
          <button key={s(row.chain_family)} type="button" onClick={() => setSelected(index)} style={{ ...panelStyle({ padding: 14, boxShadow: 'none', background: selected === index ? 'rgba(45,216,233,0.12)' : 'rgba(255,255,255,0.035)' }), color: 'var(--text)', cursor: 'pointer', textAlign: 'left' }}>
            <div style={{ fontSize: 13, fontWeight: 950 }}>{s(row.chain_family)}</div>
            <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 6 }}>Count {formatValue(row.count, 0)}. Shots {formatValue(row.shots, 0)}. Goals {formatValue(row.goals, 0)}. xT {formatValue(row.xT)}</div>
          </button>
        ))}
      </div>
      {example && (
        <div className="team-analysis-visual-grid">
          <PitchPanel title="Selected chain example" arrows={example.pitch_path as TeamPitchPoint[]} height={360} tone="violet" legend={['pass', 'carry', 'shot']} note={`${s(example.outcome)} in match ${formatValue(example.match_id, 0)}`} />
          <CompactActionList title="Action list" actions={recordList(example.actions)} />
        </div>
      )}
      {s(recordValue(chains, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(chains, 'note'))}</div>}
    </div>
  )
}

function CompactActionList({ title, actions }: { title: string; actions: GenericRow[] }) {
  return (
    <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>{title}</div>
      <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
        {actions.slice(0, 12).map((row, index) => (
          <div key={`${s(row.event_index)}-${index}`} style={{ display: 'grid', gridTemplateColumns: '28px 1fr auto', gap: 8, fontSize: 11, color: 'var(--muted)' }}>
            <strong style={{ color: 'var(--text)' }}>{formatValue(row.order ?? index + 1, 0)}</strong>
            <span>{s(row.player, 'Unknown')} {s(row.type)}</span>
            <span>{formatValue(row.minute)}</span>
          </div>
        ))}
        {!actions.length && <div style={{ color: 'var(--muted)', fontSize: 12 }}>No action list available.</div>}
      </div>
    </div>
  )
}

function GoalShotSequenceBrowser({ browser }: { browser: unknown }) {
  const [mode, setMode] = useState<SequenceBrowserMode>('goals')
  const categories = asRecord(recordValue(browser, 'categories'))
  const rows = recordList(categories[mode])
  const [selected, setSelected] = useState(0)
  const active = rows[Math.min(selected, Math.max(rows.length - 1, 0))]
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={(value) => { setMode(value); setSelected(0) }} options={SEQUENCE_BROWSER_OPTIONS} />
      <div className="team-analysis-visual-grid">
        <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
          <div style={miniLabelStyle()}>Sequences</div>
          <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
            {rows.map((row, index) => (
              <button key={`${s(row.match_id)}-${index}`} type="button" onClick={() => setSelected(index)} style={{ ...SECONDARY_BUTTON_STYLE, textAlign: 'left', background: index === selected ? 'rgba(45,216,233,0.15)' : 'rgba(255,255,255,0.045)' }}>
                Match {formatValue(row.match_id, 0)}. Minute {formatValue(row.minute)}. {s(row.outcome)}. xG {formatValue(row.xg)}. xT {formatValue(row.xT)}.
              </button>
            ))}
            {!rows.length && <div style={{ color: 'var(--muted)', fontSize: 12 }}>No sequences available for this filter.</div>}
          </div>
        </div>
        {active && <PitchPanel title="Selected sequence path" arrows={active.action_path as TeamPitchPoint[]} height={370} tone="amber" legend={['pass', 'carry', 'shot', 'goal']} note={`Players involved: ${Array.isArray(active.players_involved) ? active.players_involved.map((item) => s(item)).filter(Boolean).join(', ') : 'Not available'}`} />}
      </div>
      {active && <CompactActionList title="Selected sequence actions" actions={recordList(active.actions)} />}
    </div>
  )
}

function GoalmouthViewPanel({ view }: { view: unknown }) {
  const data = asRecord(view)
  const points = recordList(data.points)
  if (data.available === false || !points.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.note)}</div>
  return (
    <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>Goalmouth view</div>
      <svg width="100%" viewBox="0 0 420 180" style={{ display: 'block', marginTop: 12, background: 'rgba(255,255,255,0.035)', borderRadius: 16 }}>
        <rect x="54" y="28" width="312" height="112" fill="rgba(15,23,42,0.82)" stroke="rgba(255,255,255,0.20)" strokeWidth="3" />
        <line x1="54" y1="84" x2="366" y2="84" stroke="rgba(255,255,255,0.08)" />
        {points.slice(0, 160).map((row, index) => {
          const x = 54 + (Math.max(0, Math.min(100, n(row.y, 50))) / 100) * 312
          const z = 140 - (Math.max(0, Math.min(100, n(row.goal_mouth_z, 50))) / 100) * 112
          const radius = 3 + Math.min(8, n(row.xg, 0.05) * 18)
          const fill = row.is_goal ? 'rgba(34,197,94,0.90)' : s(row.outcome_type).toLowerCase().includes('save') ? 'rgba(96,165,250,0.85)' : s(row.outcome_type).toLowerCase().includes('block') ? 'rgba(251,191,36,0.82)' : 'rgba(248,113,113,0.78)'
          return <circle key={`${s(row.event_index)}-${index}`} cx={x} cy={z} r={radius} fill={fill} stroke="rgba(15,23,42,0.90)" strokeWidth="1" />
        })}
      </svg>
      <div className="team-analysis-visual-grid" style={{ marginTop: 12 }}>
        <MetricChip label="Goals" value={recordList(data.goals).length} />
        <MetricChip label="Saved shots" value={recordList(data.saved_shots).length} />
        <MetricChip label="Blocked shots" value={recordList(data.blocked_shots).length} />
        <MetricChip label="Off target shots" value={recordList(data.off_target_shots).length} />
      </div>
    </div>
  )
}

function DefensiveControlFunnelPanel({ funnel }: { funnel: unknown }) {
  const data = asRecord(funnel)
  if (data.available === false) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.warning)}</div>
  const steps = recordList(data.steps)
  const maxValue = Math.max(...steps.map((step) => n(step.value)), 1)
  const metrics = asRecord(data.metrics)
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'grid', gap: 9 }}>
        {steps.map((step) => (
          <div key={s(step.label)} style={{ display: 'grid', gridTemplateColumns: '190px 1fr auto', gap: 10, alignItems: 'center' }}>
            <div style={{ fontSize: 12, fontWeight: 900 }}>{s(step.label)}</div>
            <MiniBar value={step.value} max={maxValue} tone="red" />
            <div style={{ color: 'var(--muted)', fontSize: 12 }}>{formatValue(step.value, 0)}</div>
          </div>
        ))}
      </div>
      <div className="team-analysis-visual-grid">
        <MetricChip label="Stopped before final third" value={metrics.stopped_before_final_third} />
        <MetricChip label="Stopped before box" value={metrics.stopped_before_box} />
        <MetricChip label="Box entry to shot rate" value={metrics.box_entry_to_shot_rate} />
        <MetricChip label="Shot to goal rate" value={metrics.shot_to_goal_rate} />
        <MetricChip label="xG conceded per shot" value={metrics.xg_conceded_per_shot} />
      </div>
      {s(data.note) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(data.note)}</div>}
    </div>
  )
}

function DuelControlPanel({ duel }: { duel: unknown }) {
  const data = asRecord(duel)
  if (data.available === false) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.note)}</div>
  return (
    <div className="team-analysis-visual-grid">
      <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
        <div style={miniLabelStyle()}>Duel totals</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 10, marginTop: 12 }}>
          <MetricChip label="Total duels" value={data.total_duels} />
          <MetricChip label="Duels won" value={data.duels_won} />
          <MetricChip label="Duels lost" value={data.duels_lost} />
          <MetricChip label="Aerial won" value={data.aerial_duels_won} />
          <MetricChip label="Ground won" value={data.ground_duels_won} />
        </div>
      </div>
      <PitchPanel title="Duel locations" points={data.duel_locations as TeamPitchPoint[]} height={340} tone="green" legend={['duel', 'tackle']} />
      <SimpleRows title="Top duel players" rows={recordList(data.top_duel_players)} columns={[['player', 'Player'], ['total_duels', 'Duels'], ['duels_won', 'Won'], ['win_rate', 'Win rate']]} />
      <SimpleRows title="Duel win rate by zone" rows={recordList(data.duel_win_rate_by_zone)} columns={[['label', 'Zone'], ['duels', 'Duels'], ['win_rate', 'Win rate']]} />
    </div>
  )
}

function PressingEffectPanel({ effect }: { effect: unknown }) {
  const data = asRecord(effect)
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <CompactWarning text={data.warning} />
      <div className="team-analysis-visual-grid">
        <MetricChip label="Pressure actions" value={data.pressure_actions} />
        <MetricChip label="High regains" value={data.high_regains} />
        <MetricChip label="Forced backwards" value={data.forced_backwards} />
        <MetricChip label="Forced long" value={data.forced_long} />
        <MetricChip label="Forced out of play" value={data.forced_out_of_play} />
        <MetricChip label="Opponent escaped" value={data.opponent_escaped_pressure} />
        <MetricChip label="Shots after failed pressure" value={data.shots_conceded_after_failed_pressure} />
        <MetricChip label="Regain to shot 15s" value={data.regain_to_shot_within_15_seconds} />
        <MetricChip label="Regain to box 15s" value={data.regain_to_box_entry_within_15_seconds} />
      </div>
      {s(data.note) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(data.note)}</div>}
    </div>
  )
}

function DangerConcededSequencesPanel({ browser }: { browser: unknown }) {
  const data = asRecord(browser)
  const rows = recordList(data.sequences)
  if (data.available === false) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>{s(data.warning)}</div>
  const [selected, setSelected] = useState(0)
  const active = rows[Math.min(selected, Math.max(rows.length - 1, 0))]
  return (
    <div className="team-analysis-visual-grid">
      <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
        <div style={miniLabelStyle()}>Danger conceded sequences</div>
        <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
          {rows.slice(0, 10).map((row, index) => (
            <button key={`${s(row.chain_id)}-${index}`} type="button" onClick={() => setSelected(index)} style={{ ...SECONDARY_BUTTON_STYLE, textAlign: 'left', background: index === selected ? 'rgba(248,113,113,0.16)' : 'rgba(255,255,255,0.045)' }}>
              {s(row.danger_type)}. {s(row.problem_tag)}. Match {formatValue(row.match_id, 0)} minute {formatValue(row.minute)}.
            </button>
          ))}
        </div>
      </div>
      {active && <PitchPanel title="Selected danger path" arrows={active.action_path as TeamPitchPoint[]} height={360} tone="red" legend={['pass', 'carry', 'shot', 'goal']} />}
    </div>
  )
}

function SimpleRows({ title, rows, columns }: { title: string; rows: GenericRow[]; columns: Array<[string, string]> }) {
  return (
    <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>{title}</div>
      <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))`, gap: 8, fontSize: 10, color: 'var(--muted)', fontWeight: 850 }}>
          {columns.map(([key, label]) => <span key={key}>{label}</span>)}
        </div>
        {rows.slice(0, 8).map((row, index) => (
          <div key={`${title}-${index}`} style={{ display: 'grid', gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))`, gap: 8, fontSize: 11 }}>
            {columns.map(([key], columnIndex) => <span key={key} style={{ color: columnIndex === 0 ? 'var(--text)' : 'var(--muted)', fontWeight: columnIndex === 0 ? 850 : 600 }}>{formatValue(row[key])}</span>)}
          </div>
        ))}
        {!rows.length && <div style={{ color: 'var(--muted)', fontSize: 12 }}>No rows available.</div>}
      </div>
    </div>
  )
}

function SetPieceRoutineExamplesPanel({ profile }: { profile: unknown }) {
  const [mode, setMode] = useState<RoutineBrowserMode>('best_attacking_routines')
  const examples = asRecord(recordValue(profile, 'routine_examples'))
  const rows = recordList(examples[mode])
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <ToggleGroup value={mode} onChange={setMode} options={ROUTINE_BROWSER_OPTIONS} />
      <div className="team-analysis-visual-grid">
        {rows.slice(0, 6).map((row, index) => (
          <div key={`${s(row.routine_group)}-${index}`} style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
            <div style={{ fontSize: 13, fontWeight: 950 }}>{s(row.routine_group)}</div>
            <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.5, marginTop: 8 }}>
              Used {formatValue(row.count_used, 0)}. Shots {formatValue(row.shots_created, 0)}. Goals {formatValue(row.goals_created, 0)}. xG {formatValue(row.xg_created)}. Shot rate {formatValue(row.shot_rate, 0)}%.
            </div>
            <PitchPanel title="Routine examples" arrows={row.examples as TeamPitchPoint[]} height={240} tone={mode.includes('conceded') ? 'red' : 'amber'} legend={['set_piece', 'cross', 'shot']} />
          </div>
        ))}
      </div>
      {s(recordValue(profile, 'routine_grouping_note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(profile, 'routine_grouping_note'))}</div>}
    </div>
  )
}

function SeasonComparisonVisualPanel({ visual }: { visual: unknown }) {
  const rows = recordList(recordValue(visual, 'rows'))
  if (!rows.length) return <div style={{ color: 'var(--muted)', fontSize: 12 }}>Visual season comparison is unavailable.</div>
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {rows.map((row) => {
        const current = n(row.current_value)
        const previous = n(row.previous_value)
        const max = Math.max(current, previous, 1)
        return (
          <div key={s(row.key)} style={{ display: 'grid', gridTemplateColumns: '190px 1fr 1fr 90px', gap: 10, alignItems: 'center' }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 900 }}>{s(row.label)}</div>
              <div style={{ color: 'var(--muted)', fontSize: 10 }}>{s(row.rank_context) || 'Rank context unavailable'}</div>
            </div>
            <MiniBar value={current} max={max} tone="cyan" />
            <MiniBar value={previous} max={max} tone="amber" />
            <div style={{ color: 'var(--muted)', fontSize: 11, textAlign: 'right' }}>{s(row.trend)}</div>
          </div>
        )
      })}
      {s(recordValue(visual, 'note')) && <div style={{ color: 'var(--muted)', fontSize: 11, lineHeight: 1.45 }}>{s(recordValue(visual, 'note'))}</div>}
    </div>
  )
}

function VideoChecksPanel({ checks }: { checks: unknown }) {
  const rows = recordList(recordValue(checks, 'checks'))
  return (
    <div style={{ display: 'grid', gap: 10 }}>
      {rows.map((row) => (
        <div key={s(row.category)} style={panelStyle({ padding: 13, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
          <div style={{ fontSize: 13, fontWeight: 950 }}>{s(row.category)}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, marginTop: 6 }}>{s(row.check)}</div>
          <div style={{ color: 'var(--accent)', fontSize: 11, lineHeight: 1.4, marginTop: 6 }}>{s(row.trigger)}</div>
        </div>
      ))}
      {s(recordValue(checks, 'survivorship_bias_warning')) && <div style={{ color: '#fbbf24', fontSize: 12, lineHeight: 1.45 }}>{s(recordValue(checks, 'survivorship_bias_warning'))}</div>}
    </div>
  )
}


function LoadingSummaryCard({ startedAt }: { startedAt: number | null }) {
  return (
    <AnalysisRenderProgress
      kind="team_analysis"
      status="running"
      startedAt={startedAt}
      message="Preparing cached club profiles. Raw validation rows stay unloaded during this dashboard render."
    />
  )
}

function buildTeamSummaryCachePrefix(nation: string, tier: string, season: string): string {
  return `team-summary:${nation}:${tier}:${season}:`
}

function buildTeamSummaryCacheKey(nation: string, tier: string, season: string, team: string): string {
  return `${buildTeamSummaryCachePrefix(nation, tier, season)}${team}`
}

function clearStoredTeamSummaryCache() {
  try {
    Object.keys(sessionStorage).forEach((key) => {
      if (key.startsWith('team-summary:')) sessionStorage.removeItem(key)
    })
  } catch {
    // Browser storage can be unavailable in private sessions. The page does not depend on it.
  }

  try {
    Object.keys(localStorage).forEach((key) => {
      if (key.startsWith('team-summary:')) localStorage.removeItem(key)
    })
  } catch {
    // Browser storage can be unavailable in private sessions. The page does not depend on it.
  }
}

function readTeamSummaryCache(cacheKey: string): TeamSummaryResponse | null {
  return TEAM_SUMMARY_MEMORY_CACHE.get(cacheKey) ?? null
}

function writeTeamSummaryCache(cacheKey: string, data: TeamSummaryResponse) {
  if (TEAM_SUMMARY_MEMORY_CACHE.has(cacheKey)) TEAM_SUMMARY_MEMORY_CACHE.delete(cacheKey)
  TEAM_SUMMARY_MEMORY_CACHE.set(cacheKey, data)
  trimTeamSummaryCache()
}

function trimTeamSummaryCache() {
  const overflow = Math.max(0, TEAM_SUMMARY_MEMORY_CACHE.size - TEAM_SUMMARY_MEMORY_LIMIT)
  Array.from(TEAM_SUMMARY_MEMORY_CACHE.keys()).slice(0, overflow).forEach((key) => TEAM_SUMMARY_MEMORY_CACHE.delete(key))
}

function pruneTeamSummaryCache(nation: string, tier: string, season: string) {
  const prefix = buildTeamSummaryCachePrefix(nation, tier, season)
  Array.from(TEAM_SUMMARY_MEMORY_CACHE.keys()).forEach((key) => {
    if (!key.startsWith(prefix)) TEAM_SUMMARY_MEMORY_CACHE.delete(key)
  })
  clearStoredTeamSummaryCache()
}

export default function TeamViewerPage() {
  const [folders, setFolders] = useState<Record<string, string[]>>({})
  const [nation, setNation] = useState('England')
  const [tier, setTier] = useState('T1')
  const [season, setSeason] = useState('2025')
  const [teams, setTeams] = useState<Array<{ team: string; path: string }>>([])
  const [team, setTeam] = useState('')
  const [summary, setSummary] = useState<TeamSummaryResponse | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryRenderStartedAt, setSummaryRenderStartedAt] = useState<number | null>(null)
  const [summaryError, setSummaryError] = useState('')
  const [activeTab, setActiveTab] = useState<TeamAnalysisTab>('overview')
  const [reportView, setReportView] = useState(false)
  const [selectedPhase, setSelectedPhase] = useState('in_possession')
  const [matchId, setMatchId] = useState('')
  const [eventType, setEventType] = useState('')
  const [player, setPlayer] = useState('')
  const [rows, setRows] = useState<TableRow[]>([])
  const [columns, setColumns] = useState<string[]>([])
  const [rawLoading, setRawLoading] = useState(false)
  const [rawLoaded, setRawLoaded] = useState(false)
  const [rawError, setRawError] = useState('')
  const [scheduleSeasons, setScheduleSeasons] = useState<string[]>([])
  const [summaryReloadKey, setSummaryReloadKey] = useState(0)
  const [processing, setProcessing] = useState(false)
  const [processProgress, setProcessProgress] = useState<ProcessStreamEvent | null>(null)
  const [processLogs, setProcessLogs] = useState<ProcessStreamEvent[]>([])
  const [processPopupOpen, setProcessPopupOpen] = useState(false)
  const [processMessage, setProcessMessage] = useState('')

  const folderNames = useMemo(() => Object.keys(folders).sort(), [folders])
  const selectedFolder = `${nation} ${tier}`.trim()

  useEffect(() => {
    clearStoredTeamSummaryCache()
    getScheduleFolders()
      .then((data) => {
        setFolders(data)
        const firstFolder = Object.keys(data).sort()[0]
        if (firstFolder) {
          const firstNation = firstFolder.split(' ')[0] || 'England'
          setNation(firstNation)
          setTier(parseTierFromFolder(firstFolder, firstNation))
        }
      })
      .catch((error) => setSummaryError(asErrorMessage(error)))
  }, [])

  useEffect(() => {
    if (reportView && (activeTab === 'raw_events' || activeTab === 'data_quality')) {
      setActiveTab('overview')
    }
  }, [reportView, activeTab])

  useEffect(() => {
    getScheduleSeasons(nation, tier)
      .then((data) => {
        setScheduleSeasons(data)
        if (data.length && !data.includes(season)) setSeason(data[0])
      })
      .catch(() => setScheduleSeasons([]))
  }, [nation, tier, season])

  useEffect(() => {
    if (!nation || !tier || !season) return
    setTeams([])
    setTeam('')
    setSummary(null)
    setRows([])
    setColumns([])
    setRawLoaded(false)
    setRawError('')
    pruneTeamSummaryCache(nation, tier, season)
    getSavedTeams({ nation, tier, season })
      .then((data) => {
        setTeams(data.teams ?? [])
        const firstTeam = data.teams?.[0]?.team ?? ''
        setTeam(firstTeam)
      })
      .catch((error) => setSummaryError(asErrorMessage(error)))
  }, [nation, tier, season])

  useEffect(() => {
    if (!team) return
    const cacheKey = buildTeamSummaryCacheKey(nation, tier, season, team)
    const cached = readTeamSummaryCache(cacheKey)
    if (cached) {
      setSummary(cached)
      setSummaryError('')
      setSelectedPhase(countList(cached.phase_radar_groups)[0]?.key ?? 'in_possession')
    }

    setSummaryLoading(true)
    setSummaryRenderStartedAt(Date.now())
    setSummaryError('')
    setRows([])
    setColumns([])
    setRawLoaded(false)
    setRawError('')

    getTeamSummary({ nation, tier, season, team })
      .then((data) => {
        setSummary(data)
        setSelectedPhase(countList(data.phase_radar_groups)[0]?.key ?? 'in_possession')
        rememberAnalysisRenderDuration('team_analysis', data.render_meta?.duration_ms)
        writeTeamSummaryCache(cacheKey, data)
      })
      .catch((error) => setSummaryError(asErrorMessage(error)))
      .finally(() => setSummaryLoading(false))
  }, [nation, tier, season, team, summaryReloadKey])

  function handleStopProcessedStore() {
    setProcessMessage('Team Analysis profile store rebuild is running as one backend request. It cannot be cancelled from this view once sent.')
  }

  async function handleRebuildProcessedStore() {
    if (!nation || !tier || !season || processing) return

    setProcessing(true)
    setProcessPopupOpen(true)
    setProcessProgress({
      kind: 'status',
      stage: 'team_analysis_profile_store',
      message: 'Building Team Analysis profile store',
      percent: 8,
      completed_files: 1,
      total_files: 23,
      eta_label: 'Estimated from processed files',
    })
    setProcessLogs([
      {
        kind: 'status',
        stage: 'team_analysis_profile_store',
        message: 'Preparing cached club profiles',
        time: new Date().toLocaleTimeString(),
      },
    ])
    setProcessMessage('Building Team Analysis profile store. Estimated from processed files.')
    setSummaryError('')

    try {
      const result = await rebuildTeamAnalysisProfileStore({
        nation,
        tier,
        season,
        force: true,
      })

      const completedEvent: ProcessStreamEvent = {
        ...result,
        kind: 'complete',
        stage: 'team_analysis_profile_store_complete',
        message: 'Team Analysis profile store rebuilt. Club profile retrieval now reads from Parquet.',
        percent: 100,
        completed_files: Number(result.row_counts ? Object.keys(result.row_counts as Record<string, unknown>).length : 23),
        total_files: Number(result.row_counts ? Object.keys(result.row_counts as Record<string, unknown>).length : 23),
        rows: Number(result.row_counts && typeof result.row_counts === 'object' ? (result.row_counts as Record<string, unknown>).cleaned_season_events ?? 0 : 0),
        eta_label: 'Complete',
        time: new Date().toLocaleTimeString(),
      }

      setProcessProgress(completedEvent)
      setProcessMessage('Team Analysis profile store rebuilt. Reloading the selected club profile from Parquet.')
      setProcessLogs((prev) => [completedEvent, ...prev].slice(0, 10))
      TEAM_SUMMARY_MEMORY_CACHE.clear()
      clearStoredTeamSummaryCache()
      setSummaryReloadKey((value) => value + 1)
    } catch (error) {
      const message = asErrorMessage(error)
      const failedEvent: ProcessStreamEvent = {
        kind: 'error',
        stage: 'team_analysis_profile_store_failed',
        message,
        percent: 0,
        eta_label: 'Failed',
        time: new Date().toLocaleTimeString(),
      }
      setProcessProgress(failedEvent)
      setProcessLogs((prev) => [failedEvent, ...prev].slice(0, 10))
      setProcessMessage(message)
      setSummaryError(message)
    } finally {
      setProcessing(false)
    }
  }

  async function loadRawRows() {
    if (!team) return
    setRawLoading(true)
    setRawError('')
    try {
      const payload = await getTeamEvents({
        nation,
        tier,
        season,
        team,
        match_id: matchId.trim() ? Number(matchId) : null,
        event_type: eventType.trim() || null,
        player: player.trim() || null,
        limit: 5000,
      })
      setColumns(payload.columns)
      setRows(payload.rows)
      setRawLoaded(true)
    } catch (error) {
      setRawError(asErrorMessage(error))
    } finally {
      setRawLoading(false)
    }
  }

  const attackingProfile = summary?.attacking_profile ?? summary?.attacking
  const defensiveProfile = summary?.defensive_profile ?? summary?.defensive
  const transitionProfile = summary?.transition_profile ?? summary?.transitions
  const setPieceProfile = summary?.set_piece_profile ?? summary?.set_pieces
  const playerRows = summary?.player_profile?.players ?? summary?.players
  const seasonComparisonRows = summary?.multi_season_profile?.rows ?? []
  const dataQualityNotes = countList(summary?.data_quality?.notes)
  const phaseBreakdown = summary?.phase_kpi_breakdowns?.[selectedPhase]
  const defensiveDashboard = summary?.seasonal_defensive_dashboard as Record<string, unknown> | undefined
  const summaryPayload = (summary ?? {}) as TeamSummaryResponse & Record<string, unknown>
  const attackingAny = asRecord(attackingProfile)
  const defensiveAny = asRecord(defensiveProfile)
  const transitionAny = asRecord(transitionProfile)
  const setPieceAny = asRecord(setPieceProfile)
  const visibleTabs = reportView ? TEAM_ANALYSIS_TABS.filter((tab) => tab.key !== 'raw_events' && tab.key !== 'data_quality') : TEAM_ANALYSIS_TABS
  const shortDataWarnings = dataQualityNotes.slice(0, 2)

  return (
    <div>
      <ProcessedStoreProgressPopup
        visible={processPopupOpen || processing}
        running={processing}
        event={processProgress}
        logs={processLogs}
        message={processMessage}
        title="Team Analysis profile store"
        eyebrow={processing ? 'Building cached profiles' : 'Profile store'}
        onClose={() => setProcessPopupOpen(false)}
        onStop={handleStopProcessedStore}
      />

      <div className="page-header">
        <div>
          <div className="eyebrow">Team Profiling</div>
          <h1>Club season breakdown</h1>
          <p>A seasonal profiling dashboard built from saved WhoScored event files. It opens with summary data first and keeps raw validation rows lazy.</p>
        </div>
      </div>

      <div className="card" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12, alignItems: 'end' }}>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>
            League folder
            <select
              style={FIELD_STYLE}
              value={selectedFolder}
              onChange={(event) => {
                const folder = event.target.value
                const nextNation = folder.split(' ')[0] || nation
                setNation(nextNation)
                setTier(parseTierFromFolder(folder, nextNation))
              }}
            >
              {folderNames.map((folder) => <option key={folder} value={folder}>{folder}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>
            Season
            <select style={FIELD_STYLE} value={season} onChange={(event) => setSeason(event.target.value)}>
              {(scheduleSeasons.length ? scheduleSeasons : [season]).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>
            Team
            <select style={FIELD_STYLE} value={team} onChange={(event) => setTeam(event.target.value)}>
              {teams.map((item) => <option key={item.team} value={item.team}>{displayTeamName(item.team)}</option>)}
            </select>
          </label>
          <button
            type="button"
            onClick={handleRebuildProcessedStore}
            disabled={!nation || !tier || !season || processing}
            style={{ ...BUTTON_STYLE, opacity: !nation || !tier || !season || processing ? 0.55 : 1 }}
          >
            {processing ? 'Building profile store...' : 'Rebuild Team Analysis profile store'}
          </button>
          <button
            type="button"
            onClick={() => setReportView((value) => !value)}
            style={reportView ? BUTTON_STYLE : SECONDARY_BUTTON_STYLE}
          >
            {reportView ? 'Report view on' : 'Report view off'}
          </button>
        </div>
      </div>

      {summaryLoading && summary && (
        <AnalysisRenderProgress
          kind="team_analysis"
          status="running"
          startedAt={summaryRenderStartedAt}
          message="Refreshing the selected club profile from cached backend data. Raw rows stay unloaded until you choose to load them."
        />
      )}

      {summaryError && <div className="card" style={{ padding: 16, marginBottom: 18, color: '#fecaca' }}>{summaryError}</div>}

      {summary && (
        <>
          <div className="card" style={{ padding: 18, marginBottom: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              <div>
                <div style={miniLabelStyle()}>{summary.nation} {summary.tier} {summary.season}</div>
                <h2 style={{ margin: '6px 0 6px', fontSize: 24 }}>{displayTeamName(summary.team)}</h2>
                <div style={{ color: 'var(--muted)', fontSize: 13, lineHeight: 1.5 }}>{summary.profile?.summary_text ?? `${summary.matches} covered matches and ${summary.rows} own team rows.`}</div>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                {countList<string>(summary.style_tags).map((tag) => <StyleTag key={tag} label={tag} />)}
              </div>
            </div>
          </div>

          {reportView && (
            <div className="card" style={{ padding: 14, marginBottom: 18, border: '1px solid rgba(45,216,233,0.22)', background: 'rgba(45,216,233,0.055)' }}>
              <div style={miniLabelStyle()}>Report view</div>
              <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, marginTop: 6 }}>
                Raw validation rows, heavy data quality details and source file lists are hidden. The underlying raw rows feature is unchanged.
              </div>
              {shortDataWarnings.length > 0 && (
                <div style={{ display: 'grid', gap: 5, marginTop: 10 }}>
                  {shortDataWarnings.map((note) => <div key={note} style={{ color: '#fbbf24', fontSize: 11, lineHeight: 1.4 }}>{note}</div>)}
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
            {visibleTabs.map((tab) => <TabButton key={tab.key} tabKey={tab.key} activeTab={activeTab} onSelect={setActiveTab} label={tab.label} />)}
          </div>

          {activeTab === 'overview' && (
            <>
              <Section title="Season phase verdicts" kicker="Season level read with video checks">
                <SeasonPhaseVerdicts verdicts={summaryPayload.season_phase_verdicts} />
              </Section>

              <Section title="Seasonal summary panel" kicker="Selected team against league average, rank and previous season">
                <SeasonalSummaryPanel panel={summaryPayload.seasonal_summary_panel} />
              </Section>

              <Section title="Visual identity overview" kicker="Phase radar and KPI mix">
                <div className="team-analysis-visual-grid">
                  <PhaseRadarOverview groups={summary.phase_radar_groups} selectedKey={selectedPhase} onSelect={setSelectedPhase} />
                  <PhaseBreakdownPanel breakdown={phaseBreakdown} />
                  <SeasonalRadarComparison radar={summaryPayload.seasonal_radar} />
                </div>
              </Section>

              <Section title="League metric radar" kicker="Visual rank context, not number cards">
                <LeagueMetricStrip rows={summary.metric_radar} />
                {summary.league_context?.note && <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 12, lineHeight: 1.45 }}>{summary.league_context.note}</div>}
              </Section>

              <Section title="Style evidence panel" kicker="Tags explained by metrics and video checks">
                <StyleEvidencePanel panel={summaryPayload.style_evidence_panel} />
              </Section>

              <Section title="Seasonal momentum trend" kicker="Match by match danger and rolling five match context">
                <SeasonalMomentumPanel momentum={summaryPayload.seasonal_momentum} />
              </Section>

              <Section title="Common lineup and seasonal shapes" kicker="Position based where available">
                <CommonLineupPitch summary={summary} />
              </Section>

              <Section title="Recent match profile" kicker="Compact validation snapshot">
                <MatchLog rows={summary.match_log} />
              </Section>

              <Section title="Video checks required" kicker="Report friendly validation prompts">
                <VideoChecksPanel checks={summaryPayload.video_checks_required} />
              </Section>
            </>
          )}

          {activeTab === 'attacking' && (
            <>
              <Section title="Attacking territory" kicker="Pitch based territory profile">
                <TerritoryDashboard territory={summary.attacking_territory} />
              </Section>

              <Section title="Shot and xG maps" kicker="Bigger, cleaner shot chart">
                <ShotDashboard shotMaps={summary.shot_maps} />
              </Section>

              <Section title="Goalmouth view" kicker="Seasonal shot outcomes where goalmouth coordinates exist">
                <GoalmouthViewPanel view={attackingAny.goalmouth_view} />
              </Section>

              <Section title="Goal and shot sequence browser" kicker="Compact routes into the final action">
                <GoalShotSequenceBrowser browser={attackingAny.goal_shot_sequence_browser} />
              </Section>

              <Section title="Passes and carries" kicker="Directional arrows">
                <div className="team-analysis-visual-grid">
                  <ActionMapDashboard title="Pass map" maps={summary.pass_maps} />
                  <ActionMapDashboard title="Carry map" maps={summary.carry_maps} carry />
                </div>
              </Section>

              <Section title="Final third pass classification" kicker="Open play, danger, box entry, shot chain and recycle filters">
                <FinalThirdPassClassificationPanel classification={attackingAny.final_third_pass_classification} />
              </Section>

              <Section title="Repeated possession chains" kicker="Recurring season level possession families">
                <RepeatedPossessionChainsPanel chains={attackingAny.repeated_possession_chains} />
              </Section>

              <Section title="Goalkeeper distribution" kicker="Inferred only where data supports it">
                <GoalkeeperDistributionPanel distribution={attackingAny.goalkeeper_distribution} />
              </Section>

              <Section title="By lane stats" kicker="Pitch visual, toggleable KPI">
                <LaneStatsDashboard laneKpis={summary.lane_kpis} />
              </Section>

              <Section title="Attacking contributors" kicker="Who drives the threat">
                <div className="team-analysis-visual-grid">
                  <PlayerMiniList title="Top final third players" players={attackingProfile?.top_players} metric="final_third_entries" />
                  <PlayerMiniList title="Top creators" players={attackingProfile?.top_creators} metric="xa" />
                  <PlayerMiniList title="Top xT players" players={attackingProfile?.top_xt_players} metric="xt" />
                  <PlayerMiniList title="Top attackers" players={summary.player_profile?.top_xg_players} metric="xg" />
                </div>
              </Section>
            </>
          )}

          {activeTab === 'defensive' && (
            <Section title="Seasonal defensive dashboard" kicker="Opponent context rebuilt at club season level">
              {s(defensiveDashboard?.data_warning) && <div style={{ marginBottom: 12, color: '#fbbf24', fontSize: 12, lineHeight: 1.45 }}>{s(defensiveDashboard?.data_warning)}</div>}
              <div style={{ display: 'grid', gap: 14, marginBottom: 14 }}>
                <DefensiveControlFunnelPanel funnel={defensiveAny.control_funnel} />
                <div className="team-analysis-visual-grid">
                  <DuelControlPanel duel={defensiveAny.duel_control} />
                  <PressingEffectPanel effect={transitionAny.pressing_effect ?? defensiveAny.pressing_effect} />
                </div>
                <DangerConcededSequencesPanel browser={defensiveAny.danger_conceded_sequences} />
              </div>
              <div className="team-analysis-visual-grid">
                <PitchPanel title="Shots conceded map" points={(defensiveDashboard?.shots_conceded_map as TeamPitchPoint[]) ?? defensiveProfile?.shots_conceded_locations} showGoalRings height={380} tone="red" legend={['shot', 'goal']} />
                <PitchPanel title="xG conceded map" points={(defensiveDashboard?.xg_conceded_map as TeamPitchPoint[]) ?? defensiveProfile?.xg_conceded_shot_quality_map} showGoalRings height={380} tone="red" legend={['shot', 'goal']} />
                <PitchPanel title="Defensive action heatmap" heatmap={defensiveDashboard?.defensive_action_heatmap as TeamHeatmap} points={defensiveProfile?.defensive_action_locations} height={360} tone="green" legend={['tackle', 'interception', 'recovery']} />
                <PitchPanel title="High regains map" points={(defensiveDashboard?.high_regains_map as TeamPitchPoint[]) ?? defensiveProfile?.high_regain_locations} height={360} tone="green" legend={['tackle', 'interception', 'recovery']} />
                <PitchPanel title="Opponent box entry map" points={defensiveDashboard?.opponent_box_entry_map as TeamPitchPoint[]} height={360} tone="red" legend={['pass', 'carry', 'cross']} />
                <PitchPanel title="Opponent final third entries" points={defensiveDashboard?.opponent_final_third_entries as TeamPitchPoint[]} height={360} tone="red" legend={['pass', 'carry', 'cross']} />
                <PitchPanel title="Pressure and defensive zones" lanes={defensiveDashboard?.pressure_or_defensive_action_zones as TeamLaneSummary[]} height={340} tone="green" legend={['tackle', 'interception']} />
                <PitchPanel title="Danger conceded pitch view" heatmap={(defensiveDashboard?.danger_conceded_pitch_view as TeamHeatmap) ?? defensiveProfile?.danger_conceded_heatmap} height={340} tone="red" legend={['pass', 'carry', 'shot']} />
                <PitchPanel title="Defensive transition threat conceded" heatmap={defensiveDashboard?.defensive_transition_threat_conceded as TeamHeatmap} points={defensiveProfile?.opponent_xt_threat_map} height={340} tone="red" legend={['pass', 'carry']} />
                <PitchPanel title="Set piece danger conceded" points={(defensiveDashboard?.set_piece_danger_conceded as TeamPitchPoint[]) ?? setPieceProfile?.defensive_set_piece_shot_locations} showGoalRings height={340} tone="red" legend={['set_piece', 'shot', 'goal']} />
              </div>
              <div className="team-analysis-visual-grid" style={{ marginTop: 14 }}>
                <PlayerMiniList title="Defensive stabilisers" players={defensiveProfile?.top_players} metric="defensive_actions" />
                <PlayerMiniList title="High regain players" players={transitionProfile?.top_players} metric="high_regains" />
              </div>
              {defensiveProfile?.interpretation_note && <div style={{ marginTop: 14, color: 'var(--muted)', fontSize: 12, lineHeight: 1.5 }}>{defensiveProfile.interpretation_note}</div>}
            </Section>
          )}

          {activeTab === 'transitions' && (
            <Section title="Transitions" kicker="Regains and immediate threat">
              <div className="team-analysis-visual-grid">
                <PitchPanel title="High regain map" points={transitionProfile?.regain_locations} tone="green" height={360} />
                <PitchPanel title="Opponent high regains against" points={transitionProfile?.opponent_high_regain_locations} tone="red" height={360} />
                <PitchPanel title="Opponent transition threat" heatmap={transitionProfile?.opponent_transition_heatmap} tone="red" height={360} />
                <PitchPanel title="Opponent xT after regain threat" points={transitionProfile?.opponent_xt_threat_map} tone="red" height={360} />
                <PressingEffectPanel effect={transitionAny.pressing_effect} />
                <PlayerMiniList title="Top transition players" players={transitionProfile?.top_players} metric="high_regains" why={transitionProfile?.note} />
                <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
                  <div style={miniLabelStyle()}>Transition summary</div>
                  <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                    <MetricChip label="High regains" value={transitionProfile?.high_regains} />
                    <MetricChip label="Regain to attack" value={transitionProfile?.regain_to_attack_sequences} />
                    <MetricChip label="Opponent transition threat" value={transitionProfile?.opponent_transition_threat} />
                  </div>
                </div>
              </div>
            </Section>
          )}

          {activeTab === 'set_pieces' && (
            <Section title="Set pieces" kicker="Throw ins, corners and free kicks">
              <div style={{ display: 'grid', gap: 14 }}>
                <SetPieceRoutineExamplesPanel profile={setPieceAny} />
                <div className="team-analysis-visual-grid">
                  <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
                    <div style={miniLabelStyle()}>Set piece attacking threat</div>
                    <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, marginTop: 8 }}>
                      Volume, deliveries and threat are split by type below. Delivery line intensity uses xT, xA, xG or dangerous territory fallback.
                    </div>
                  </div>
                  <div style={panelStyle({ padding: 15, boxShadow: 'none', background: 'rgba(255,255,255,0.035)' })}>
                    <div style={miniLabelStyle()}>Defensive set piece context</div>
                    <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, marginTop: 8 }}>
                      Conceded maps use available opponent rows. Where opponent rows are missing, the data quality tab explains the gap.
                    </div>
                  </div>
                </div>
                <SetPieceSectionCard title="Throw ins" section={setPieceProfile?.throw_ins} />
                <SetPieceSectionCard title="Corners" section={setPieceProfile?.corners} />
                <SetPieceSectionCard title="Free kicks" section={setPieceProfile?.free_kicks} />
              </div>
            </Section>
          )}

          {activeTab === 'players' && (
            <Section title="Player influence dashboard" kicker="Role and influence categories before raw contribution">
              <PlayerInfluenceDashboard categories={summary.player_influence_dashboard?.categories} />
              <details style={{ marginTop: 16 }}>
                <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 900, color: 'var(--accent)' }}>Open raw contribution table</summary>
                <div style={{ marginTop: 14 }}>
                  <PlayerContributionTable players={playerRows} />
                </div>
              </details>
            </Section>
          )}

          {activeTab === 'season_comparison' && (
            <Section title="Season comparison" kicker="Available previous seasons with compact visual trends">
              <SeasonComparisonVisualPanel visual={summaryPayload.season_comparison_visual} />
              <div style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.5, margin: '14px 0' }}>{summary.multi_season_profile?.note}</div>
              <SeasonComparisonTable rows={seasonComparisonRows} />
            </Section>
          )}

          {activeTab === 'data_quality' && (
            <Section title="Data quality" kicker="Saved file coverage and opponent context audit">
              <div className="team-analysis-visual-grid">
                <MetricChip label="Load mode" value={summary.data_quality?.load_mode} />
                <MetricChip label="Own team rows" value={summary.data_quality?.own_team_rows} />
                <MetricChip label="Opponent rows" value={summary.data_quality?.opponent_rows} />
                <MetricChip label="Matches covered" value={summary.data_quality?.match_ids_covered?.length ?? 0} />
                <MetricChip label="Matches with opponent rows" value={summary.data_quality?.matches_with_opponent_rows?.length ?? 0} />
                <MetricChip label="Matches without opponent rows" value={summary.data_quality?.matches_without_opponent_rows?.length ?? 0} />
                <MetricChip label="League teams compared" value={summary.data_quality?.league_teams_compared} />
                <MetricChip label="xG model status" value={s(summary.data_quality?.xg_model_status?.status)} />
              </div>
              <div style={{ marginTop: 18 }}>
                <div style={miniLabelStyle()}>Notes</div>
                <div style={{ display: 'grid', gap: 7, marginTop: 10 }}>
                  {dataQualityNotes.length ? dataQualityNotes.map((note) => <div key={note} style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45 }}>{note}</div>) : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No data quality notes were returned.</div>}
                </div>
              </div>
              <div style={{ marginTop: 18 }}>
                <div style={miniLabelStyle()}>Source files used</div>
                <div style={{ display: 'grid', gap: 7, marginTop: 10 }}>
                  {countList(summary.data_quality?.source_files_used).length ? countList(summary.data_quality?.source_files_used).map((filePath) => <div key={filePath} style={{ color: 'var(--muted)', fontSize: 12, lineHeight: 1.45, wordBreak: 'break-all' }}>{filePath}</div>) : <div style={{ color: 'var(--muted)', fontSize: 12 }}>No source file list was returned.</div>}
                </div>
              </div>
            </Section>
          )}

          {activeTab === 'raw_events' && (
            <Section title="Raw validation rows" kicker="Validation rows load only on demand">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr)) auto auto', gap: 10, alignItems: 'end' }}>
                <label style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Match id filter
                  <input style={FIELD_STYLE} value={matchId} onChange={(event) => setMatchId(event.target.value)} placeholder="Optional" />
                </label>
                <label style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Event type filter
                  <input style={FIELD_STYLE} value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="Shot, Pass, Tackle" />
                </label>
                <label style={{ fontSize: 12, color: 'var(--muted)' }}>
                  Player filter
                  <input style={FIELD_STYLE} value={player} onChange={(event) => setPlayer(event.target.value)} placeholder="Player name" />
                </label>
                <button type="button" style={BUTTON_STYLE} onClick={loadRawRows} disabled={rawLoading}>{rawLoading ? 'Loading rows...' : rawLoaded ? 'Apply raw filters' : 'Load raw validation rows'}</button>
                {rawLoaded && <button type="button" style={SECONDARY_BUTTON_STYLE} onClick={() => { setRows([]); setColumns([]); setRawLoaded(false); setRawError('') }}>Unload rows</button>}
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 10 }}>
                Default raw preview uses 500 rows. Filtered validation requests can load a larger preview without blocking the dashboard.
              </div>
              {rawError && <div style={{ marginTop: 12, color: '#fecaca', fontSize: 12 }}>{rawError}</div>}
              {rawLoaded ? (
                <div style={{ marginTop: 14 }}>
                  <DataTable columns={columns} rows={rows} maxRows={700} height={500} />
                </div>
              ) : (
                <div style={{ marginTop: 14, color: 'var(--muted)', fontSize: 12 }}>Raw rows are currently unloaded.</div>
              )}
            </Section>
          )}
        </>
      )}

      {!summary && summaryLoading && <LoadingSummaryCard startedAt={summaryRenderStartedAt} />}
      {!summary && !summaryLoading && <div className="card" style={{ padding: 34, color: 'var(--muted)', textAlign: 'center' }}>Select a team to open the club profiling dashboard.</div>}
    </div>
  )
}