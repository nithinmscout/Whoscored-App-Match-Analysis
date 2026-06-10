import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import AnalysisRenderProgress, { rememberAnalysisRenderDuration } from './AnalysisRenderProgress'
import ProcessedStoreProgressPopup from './ProcessedStoreProgressPopup'
import DataTable from './DataTable'
import { EmptyPitchNote, EventLegend, PitchArrowLayer, PitchCanvas, PitchHeatLayer, PitchLaneLayer, PitchPointLayer, PitchSequenceLayer, SvgPitchTooltip, useSvgPitchTooltip } from './EventPitch'
import { getMatchAnalysis, getScheduleFolders, getScheduleSeasons, openProcessedStoreStream } from '../lib/api'
import type { MatchAnalysisResponse, MatchFixture, TableRow } from '../types/api'
import { pitchX, pitchY } from '../lib/eventVizSpec'

type AnyRecord = Record<string, unknown>
type Side = 'home' | 'away'
type GameStateFilter = 'all' | 'first_half' | 'second_half' | 'before_first_goal' | 'after_first_goal' | 'level_score' | 'selected_team_leading' | 'selected_team_trailing' | 'after_first_red_card' | 'after_first_substitution'
type PerspectiveFilter = 'home' | 'away'
type ShotContactFilter = 'all' | 'ground' | 'headed'

const GAME_STATE_OPTIONS: Array<{ value: GameStateFilter; label: string }> = [
  { value: 'all', label: 'Full game' },
  { value: 'first_half', label: 'First half' },
  { value: 'second_half', label: 'Second half' },
  { value: 'before_first_goal', label: 'Before first goal' },
  { value: 'after_first_goal', label: 'After first goal' },
  { value: 'level_score', label: 'Level score' },
  { value: 'selected_team_leading', label: 'Selected team leading' },
  { value: 'selected_team_trailing', label: 'Selected team trailing' },
  { value: 'after_first_red_card', label: 'After first red card' },
  { value: 'after_first_substitution', label: 'After first substitution' },
]

const CHAIN_CATEGORIES: Array<{ value: string; label: string }> = [
  { value: 'best_attacking_chains', label: 'Best attacking chains' },
  { value: 'long_build_ups', label: 'Long build ups' },
  { value: 'direct_attacks', label: 'Direct attacks' },
  { value: 'failed_progressions', label: 'Failed progressions' },
  { value: 'chains_ending_in_shot', label: 'Chains ending in shot' },
  { value: 'chains_ending_in_box_entry', label: 'Chains ending in box entry' },
]

const FIFTEEN_MINUTE_BUCKETS = ['0-15', '16-30', '31-45', '46-60', '61-75', '76-90']

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

function panelStyle(extra?: CSSProperties): CSSProperties {
  return {
    border: '1px solid rgba(255,255,255,0.08)',
    background: 'linear-gradient(180deg, rgba(17,24,39,0.96), rgba(10,15,26,0.96))',
    borderRadius: 20,
    boxShadow: '0 18px 55px rgba(0,0,0,0.24)',
    ...extra,
  }
}

function buttonStyle(active = false): CSSProperties {
  return {
    border: active ? '1px solid rgba(45,216,233,0.7)' : '1px solid rgba(255,255,255,0.12)',
    background: active ? 'rgba(45,216,233,0.18)' : 'rgba(255,255,255,0.045)',
    color: 'var(--text)',
    borderRadius: 12,
    padding: '10px 12px',
    cursor: 'pointer',
    fontWeight: 850,
    fontSize: 12,
  }
}

function labelStyle(): CSSProperties {
  return {
    fontSize: 11,
    color: 'var(--muted)',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    fontWeight: 850,
  }
}

function titleStyle(): CSSProperties {
  return {
    margin: 0,
    fontSize: 17,
    fontWeight: 950,
    letterSpacing: -0.2,
  }
}

function asErrorMessage(error: unknown): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError?.response?.data?.detail ?? anyError?.message ?? 'Unknown error'
}

function n(value: unknown, fallback = 0): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function s(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
}

function cleanVisibleText(value: unknown): string {
  const text = s(value).trim()
  return text && !['nan', 'none', 'null', 'undefined', '<na>'].includes(text.toLowerCase()) ? text : ''
}

function playerInitials(value: unknown): string {
  const words = cleanVisibleText(value)
    .replace(/[0-9]+/g, ' ')
    .split(/\s+/)
    .map((word) => word.replace(/[^A-Za-zÀ-ÖØ-öø-ÿ]/g, ''))
    .filter(Boolean)
  return words.slice(0, 2).map((word) => word.charAt(0).toUpperCase()).join('')
}

function playerShortName(value: unknown): string {
  const words = cleanVisibleText(value).split(/\s+/).filter(Boolean)
  return words.length ? words[words.length - 1] : ''
}

function getPlayerNodeLabel(player: AnyRecord | undefined, fallbackName?: unknown): string {
  const shirtCandidates = [player?.shirt_no, player?.shirtNo, player?.shirt_number, player?.shirtNumber, player?.jersey_number, player?.jerseyNumber]
  for (const candidate of shirtCandidates) {
    const text = cleanVisibleText(candidate)
    if (!text) continue
    const numeric = Number(text)
    if (Number.isFinite(numeric)) return Number.isInteger(numeric) ? String(numeric) : String(Number(numeric.toFixed(1)))
    return text
  }
  return playerInitials(player?.player ?? player?.name ?? fallbackName)
}

function b(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value > 0
  return ['true', '1', 'yes', 'y', 'successful', 'success', 'won'].includes(s(value).trim().toLowerCase())
}

function shotContactType(point: AnyRecord): Exclude<ShotContactFilter, 'all'> {
  const explicit = s(point.shot_contact ?? point.shot_body_part ?? point.shot_type).toLowerCase().replace(/[\s_-]+/g, '')
  if (explicit.includes('head') || explicit.includes('aerial')) return 'headed'
  if (explicit.includes('ground') || explicit.includes('foot')) return 'ground'

  const text = [
    point.type,
    point.outcome_type,
    point.goal_mouth_qualifiers,
    point.qualifier_tags,
    point.qual_tags,
  ].map((value) => s(value).toLowerCase()).join(' ')

  return text.includes('header') || text.includes('headed') || text.includes('aerial') ? 'headed' : 'ground'
}

function pctCoord(value: unknown): number | null {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return null
  return Math.max(0, Math.min(100, numeric))
}

function buildPitchHeatmap(events: AnyRecord[], source: 'start' | 'end' = 'end', valueKey?: string): AnyRecord {
  const xBins = 6
  const yBins = 5
  const cells = new Map<string, { x_bin: number; y_bin: number; count: number; value: number }>()

  events.forEach((event) => {
    const x = pctCoord(source === 'end' ? event.end_x ?? event.x : event.start_x ?? event.x)
    const y = pctCoord(source === 'end' ? event.end_y ?? event.y : event.start_y ?? event.y)
    if (x === null || y === null) return

    const value = valueKey ? Math.max(0, n(event[valueKey])) : 1
    if (valueKey && value <= 0) return

    const xBin = Math.min(xBins - 1, Math.max(0, Math.floor((x / 100) * xBins)))
    const yBin = Math.min(yBins - 1, Math.max(0, Math.floor((y / 100) * yBins)))
    const key = `${xBin}:${yBin}`
    const current = cells.get(key) ?? { x_bin: xBin, y_bin: yBin, count: 0, value: 0 }
    current.count += 1
    current.value += value
    cells.set(key, current)
  })

  return {
    x_bins: xBins,
    y_bins: yBins,
    cells: Array.from(cells.values()).map((cell) => ({
      ...cell,
      value: Number(cell.value.toFixed(valueKey ? 3 : 0)),
    })),
  }
}

function HeatFunnelToggle({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--muted)', cursor: 'pointer', userSelect: 'none' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        style={{ width: 13, height: 13, accentColor: 'var(--accent)' }}
      />
      Heat funnel
    </label>
  )
}


function parseTierFromFolder(folder: string, nation: string): string {
  return folder.replace(nation, '').trim() || 'T1'
}

function toRows(rows: unknown[]): TableRow[] {
  return rows.map((row) => row as TableRow)
}

function fixtureLabel(fixture: MatchFixture): string {
  const score = fixture.home_score === null || fixture.away_score === null || fixture.home_score === undefined || fixture.away_score === undefined
    ? ''
    : ` ${fixture.home_score}:${fixture.away_score}`
  return `${fixture.home_team} v ${fixture.away_team}${score}`
}

function formatKickoff(value: unknown): string {
  const text = s(value)
  if (!text) return 'Kick off unavailable'
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? text : date.toLocaleString()
}

function metric(summary: AnyRecord | undefined, key: string): number {
  return n(summary?.[key])
}

function MetricCard({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div style={panelStyle({ padding: 15 })}>
      <div style={labelStyle()}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 950, marginTop: 6 }}>{value}</div>
      {note && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 5, lineHeight: 1.4 }}>{note}</div>}
    </div>
  )
}

function CompareBar({ home, away, homeLabel, awayLabel }: { home: number; away: number; homeLabel: string; awayLabel: string }) {
  const total = Math.max(home + away, 1)
  const homePct = Math.max(3, (home / total) * 100)
  const awayPct = Math.max(3, (away / total) * 100)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
        <span>{homeLabel}</span>
        <span>{awayLabel}</span>
      </div>
      <div style={{ height: 10, borderRadius: 999, overflow: 'hidden', background: 'rgba(255,255,255,0.08)', display: 'flex' }}>
        <div style={{ width: `${homePct}%`, background: 'linear-gradient(90deg, rgba(45,216,233,0.95), rgba(45,216,233,0.4))' }} />
        <div style={{ width: `${awayPct}%`, background: 'linear-gradient(90deg, rgba(167,139,250,0.4), rgba(167,139,250,0.95))' }} />
      </div>
    </div>
  )
}


function recordArray(value: unknown): AnyRecord[] {
  return Array.isArray(value) ? value.filter((item): item is AnyRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : []
}

function minuteText(value: unknown): string {
  const minute = n(value, Number.NaN)
  return Number.isFinite(minute) ? `${Math.round(minute)}'` : 'N/A'
}

function valueText(value: unknown, decimals = 1): string {
  const numeric = n(value, Number.NaN)
  return Number.isFinite(numeric) ? numeric.toFixed(decimals) : 'N/A'
}

function intervalRowsFromProfile(profile: AnyRecord): AnyRecord[] {
  const directRows = recordArray(profile.fifteen_minute_intervals)
  if (directRows.length) return directRows

  const phase = (profile.phase_profile ?? {}) as AnyRecord
  return [
    { bucket: '0-15', net: n(phase.start_0_15) },
    { bucket: '16-30', net: n(phase.first_half_16_45) },
    { bucket: '31-45', net: n(phase.first_half_16_45) },
    { bucket: '46-60', net: n(phase.second_half_46_75) },
    { bucket: '61-75', net: n(phase.second_half_46_75) },
    { bucket: '76-90', net: n(phase.late_76_90) },
  ]
}

function intervalNetValue(profile: AnyRecord, bucket: string): number {
  const row = intervalRowsFromProfile(profile).find((item) => s(item.bucket) === bucket)
  return n(row?.net)
}

function MomentumRecentIntervalChart({ title, data, tone }: { title: string; data: AnyRecord | undefined; tone: 'home' | 'away' }) {
  const recentMatches = recordArray(data?.recent_matches).slice(0, 5).reverse()
  const width = 820
  const height = 260
  const left = 40
  const right = 18
  const top = 24
  const bottom = 46
  const chartWidth = width - left - right
  const chartHeight = height - top - bottom
  const zeroY = top + chartHeight / 2
  const accent = tone === 'home' ? 'rgba(45,216,233,0.96)' : 'rgba(167,139,250,0.96)'
  const values = recentMatches.flatMap((match) => FIFTEEN_MINUTE_BUCKETS.map((bucket) => intervalNetValue(match, bucket)))
  const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)))
  const groupWidth = chartWidth / FIFTEEN_MINUTE_BUCKETS.length
  const barWidth = Math.max(5, Math.min(13, (groupWidth - 20) / Math.max(recentMatches.length, 1)))

  return (
    <div style={panelStyle({ padding: 15, minWidth: 0 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>{title}</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 4 }}>Last five processed matches. Positive bars show the team's momentum edge in that spell.</div>
        </div>
        <div style={{ ...labelStyle(), color: accent }}>{recentMatches.length}/5 matches</div>
      </div>

      {recentMatches.length === 0 ? (
        <div style={{ ...smallInfoStyle(), padding: 14, border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, background: 'rgba(255,255,255,0.035)' }}>Build the processed store to render the previous five match momentum bars.</div>
      ) : (
        <>
          <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 260, display: 'block' }}>
            <rect x={left} y={top} width={chartWidth} height={chartHeight} rx="14" fill="rgba(255,255,255,0.035)" />
            <line x1={left} x2={left + chartWidth} y1={zeroY} y2={zeroY} stroke="rgba(255,255,255,0.22)" strokeWidth="1" />
            {[0.5, 1].map((ratio) => (
              <g key={ratio}>
                <line x1={left} x2={left + chartWidth} y1={zeroY - (chartHeight / 2) * ratio} y2={zeroY - (chartHeight / 2) * ratio} stroke="rgba(255,255,255,0.055)" />
                <line x1={left} x2={left + chartWidth} y1={zeroY + (chartHeight / 2) * ratio} y2={zeroY + (chartHeight / 2) * ratio} stroke="rgba(255,255,255,0.055)" />
              </g>
            ))}
            {FIFTEEN_MINUTE_BUCKETS.map((bucket, bucketIndex) => {
              const groupX = left + bucketIndex * groupWidth
              const groupCentre = groupX + groupWidth / 2
              const barsWidth = recentMatches.length * barWidth + Math.max(0, recentMatches.length - 1) * 3
              return (
                <g key={bucket}>
                  {bucketIndex > 0 && <line x1={groupX} x2={groupX} y1={top} y2={top + chartHeight} stroke="rgba(255,255,255,0.055)" />}
                  <text x={groupCentre} y={height - 18} fill="rgba(232,234,240,0.68)" fontSize="12" fontWeight="800" textAnchor="middle">{bucket}</text>
                  {recentMatches.map((match, matchIndex) => {
                    const value = intervalNetValue(match, bucket)
                    const barHeight = Math.min(chartHeight / 2, Math.abs(value) / maxAbs * (chartHeight / 2 - 12))
                    const x = groupCentre - barsWidth / 2 + matchIndex * (barWidth + 3)
                    const y = value >= 0 ? zeroY - barHeight : zeroY
                    const fill = value >= 0 ? accent : 'rgba(248,113,113,0.82)'
                    return (
                      <g key={`${s(match.match_id, String(matchIndex))}-${bucket}`}>
                        <rect x={x} y={y} width={barWidth} height={Math.max(1, barHeight)} rx="3" fill={fill} opacity={0.82} />
                        <title>{`${s(match.match_date, 'Recent match')} vs ${s(match.opponent, 'opponent')} ${s(match.scoreline)} | ${bucket}: ${value.toFixed(2)}`}</title>
                      </g>
                    )
                  })}
                </g>
              )
            })}
            <text x={left - 10} y={top + 4} fill="rgba(232,234,240,0.54)" fontSize="10" textAnchor="end">+</text>
            <text x={left - 10} y={top + chartHeight - 2} fill="rgba(232,234,240,0.54)" fontSize="10" textAnchor="end">-</text>
          </svg>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, justifyContent: 'flex-end', marginTop: 6 }}>
            {recentMatches.map((match, index) => (
              <span key={`${s(match.match_id, String(index))}-legend`} style={{ fontSize: 10, color: 'var(--muted)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 999, padding: '5px 8px', background: 'rgba(255,255,255,0.035)' }}>
                G{index + 1}: {s(match.opponent, 'Opponent')} {s(match.scoreline) ? `(${s(match.scoreline)})` : ''}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function MomentumChip({ label, value, tone = 'neutral' }: { label: string; value: string | number; tone?: 'home' | 'away' | 'good' | 'bad' | 'neutral' }) {
  const color = tone === 'home' ? 'rgba(45,216,233,0.96)' : tone === 'away' ? 'rgba(167,139,250,0.96)' : tone === 'good' ? '#86efac' : tone === 'bad' ? '#fca5a5' : 'var(--text)'
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.09)', background: 'rgba(255,255,255,0.045)', borderRadius: 14, padding: '10px 11px', minWidth: 0 }}>
      <div style={labelStyle()}>{label}</div>
      <div style={{ marginTop: 5, fontSize: 15, fontWeight: 950, color, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
    </div>
  )
}

function MomentumWindowList({ title, items, valueKey, tone }: { title: string; items: AnyRecord[]; valueKey: string; tone: 'home' | 'away' | 'good' | 'bad' | 'neutral' }) {
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 16, padding: 12, background: 'rgba(255,255,255,0.035)', minWidth: 0 }}>
      <div style={{ ...labelStyle(), marginBottom: 9 }}>{title}</div>
      {items.length ? (
        <div style={{ display: 'grid', gap: 7 }}>
          {items.slice(0, 3).map((item, index) => (
            <div key={`${title}-${index}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, color: 'var(--muted)', alignItems: 'center' }}>
              <span style={{ color: 'var(--text)', fontWeight: 850 }}>{minuteText(item.minute)}</span>
              <span>{s(item.bucket, 'window')}</span>
              <span style={{ color: tone === 'bad' ? '#fca5a5' : tone === 'away' ? 'rgba(167,139,250,0.96)' : tone === 'home' || tone === 'good' ? 'rgba(45,216,233,0.96)' : 'var(--text)', fontWeight: 900 }}>{valueText(item[valueKey], 2)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>No clear windows found.</div>
      )}
    </div>
  )
}

function MomentumBucketList({ title, items, tone }: { title: string; items: AnyRecord[]; tone: 'home' | 'away' | 'good' | 'bad' | 'neutral' }) {
  const accent = tone === 'away' ? 'rgba(167,139,250,0.96)' : tone === 'bad' ? '#fca5a5' : tone === 'good' || tone === 'home' ? 'rgba(45,216,233,0.96)' : 'var(--text)'
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 16, padding: 12, background: 'rgba(255,255,255,0.03)', minWidth: 0 }}>
      <div style={{ ...labelStyle(), marginBottom: 9 }}>{title}</div>
      {items.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {items.slice(0, 6).map((item, index) => (
            <span key={`${title}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.10)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>
              <span style={{ color: accent, fontWeight: 950 }}>{s(item.bucket)}</span> {n(item.count)}x
            </span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>No matching recent windows.</div>
      )}
    </div>
  )
}

function MomentumGoalStrip({ title, goals, tone }: { title: string; goals: AnyRecord[]; tone: 'home' | 'away' | 'good' | 'bad' | 'neutral' }) {
  const accent = tone === 'bad' ? '#fca5a5' : tone === 'away' ? 'rgba(167,139,250,0.96)' : 'rgba(45,216,233,0.96)'
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 16, padding: 12, background: 'rgba(255,255,255,0.03)', minWidth: 0 }}>
      <div style={{ ...labelStyle(), marginBottom: 9 }}>{title}</div>
      {goals.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
          {goals.map((goal, index) => (
            <span key={`${title}-${index}`} style={{ border: `1px solid ${accent}`, borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--text)', background: 'rgba(255,255,255,0.035)', fontWeight: 850 }}>
              {minuteText(goal.minute)} {s(goal.player)} {s(goal.score_after_event) ? `(${s(goal.score_after_event)})` : ''}
            </span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>No goals in this group.</div>
      )}
    </div>
  )
}

function MomentumTeamAnalysisCard({ title, data, tone }: { title: string; data: AnyRecord | undefined; tone: 'home' | 'away' }) {
  const selected = (data?.selected_match ?? {}) as AnyRecord
  const summary = (data?.summary ?? {}) as AnyRecord
  const recentMatches = recordArray(data?.recent_matches)
  const peaks = recordArray(selected.peaks)
  const troughs = recordArray(selected.troughs)
  const goalsFor = recordArray(selected.goals_for)
  const goalsAgainst = recordArray(selected.goals_against)
  const similarPeaks = recordArray(summary.similar_peak_windows)
  const similarTroughs = recordArray(summary.similar_trough_windows)
  const recentGoalForBuckets = recordArray(summary.recent_goal_for_buckets)
  const recentGoalAgainstBuckets = recordArray(summary.recent_goal_against_buckets)
  const accent = tone === 'home' ? 'rgba(45,216,233,0.96)' : 'rgba(167,139,250,0.96)'

  const strongestPeak = peaks[0]
  const worstTrough = troughs[0]
  const peakPhrase = strongestPeak
    ? `Their clearest surge came around ${minuteText(strongestPeak.minute)}, usually the spell to rewatch first.`
    : 'No strong attacking surge stood out in the current momentum curve.'
  const troughPhrase = worstTrough
    ? `The main dip came around ${minuteText(worstTrough.minute)}, when the opponent carried the greater threat.`
    : 'No clear drop off window stood out from the selected match.'
  const recentPeakPhrase = similarPeaks.length
    ? `Recent matches show similar pressure spikes in ${Array.from(new Set(similarPeaks.slice(0, 4).map((item) => s(item.bucket)))).filter(Boolean).join(', ')}.`
    : 'Recent matches do not show a repeated peak pattern matching this game.'
  const recentTroughPhrase = similarTroughs.length
    ? 'The same type of dip has appeared recently, so it is worth checking whether it links to game state, fatigue or opposition territory.'
    : 'The trough pattern is not repeated strongly in the recent sample.'
  const goalForPhrase = goalsFor.length
    ? `Goals scored: ${goalsFor.map((goal) => `${minuteText(goal.minute)} ${s(goal.player)}`.trim()).join(', ')}.`
    : 'No goals scored in this match.'
  const goalAgainstPhrase = goalsAgainst.length
    ? `Goals conceded: ${goalsAgainst.map((goal) => `${minuteText(goal.minute)} ${s(goal.player)}`.trim()).join(', ')}.`
    : 'No goals conceded in this match.'
  const commonGoalFor = recentGoalForBuckets[0]
  const commonGoalAgainst = recentGoalAgainstBuckets[0]

  return (
    <div style={panelStyle({ padding: 15, minWidth: 0 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>{title}</h3>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4 }}>
            {s(selected.venue, 'Venue N/A')} against {s(selected.opponent, 'opponent N/A')} {s(selected.scoreline) ? `(${s(selected.scoreline)})` : ''}
          </div>
        </div>
        <div style={{ textAlign: 'right', fontSize: 11, color: 'var(--muted)' }}>
          Recent sample<br /><span style={{ color: accent, fontWeight: 950 }}>{recentMatches.length || n(summary.recent_match_count)}</span> matches
        </div>
      </div>

      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ border: `1px solid ${accent.replace('0.96', '0.24')}`, background: 'rgba(255,255,255,0.035)', borderRadius: 16, padding: 12 }}>
          <div style={{ ...labelStyle(), color: accent }}>Current match read</div>
          <div style={{ marginTop: 7, display: 'grid', gap: 6, fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
            <span>{peakPhrase}</span>
            <span>{troughPhrase}</span>
            <span>{goalForPhrase} {goalAgainstPhrase}</span>
          </div>
        </div>

        <div style={{ border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.03)', borderRadius: 16, padding: 12 }}>
          <div style={labelStyle()}>Previous five match pattern</div>
          <div style={{ marginTop: 7, display: 'grid', gap: 6, fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
            <span>{recentPeakPhrase}</span>
            <span>{recentTroughPhrase}</span>
            <span>
              {commonGoalFor ? `Most common scoring spell recently: ${s(commonGoalFor.bucket)}. ` : ''}
              {commonGoalAgainst ? `Most common concession spell recently: ${s(commonGoalAgainst.bucket)}.` : ''}
              {!commonGoalFor && !commonGoalAgainst ? 'There is no clear repeated goal timing pattern in the recent processed sample.' : ''}
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

function MomentumAnalysisPanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const block = ((analysis as unknown as AnyRecord).momentum_analysis ?? {}) as AnyRecord
  const home = (block.home ?? {}) as AnyRecord
  const away = (block.away ?? {}) as AnyRecord
  const selected = analysis.selected_fixture

  if (!home.available && !away.available) return null

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>Previous five match momentum</h3>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>15 minute interval bars. Each thin bar is one recent match.</div>
        </div>
        <div style={{ ...labelStyle(), textAlign: 'right' }}>Visual trend</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 14 }}>
        <MomentumRecentIntervalChart title={s(selected?.home_team, 'Home')} data={home} tone="home" />
        <MomentumRecentIntervalChart title={s(selected?.away_team, 'Away')} data={away} tone="away" />
      </div>
    </div>
  )
}

function MomentumChart({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [view, setView] = useState<'danger' | 'possession'>('danger')
  const chartTooltip = useSvgPitchTooltip()
  const points = analysis.momentum ?? []
  const markers = analysis.match_markers ?? []
  const selected = analysis.selected_fixture
  const width = 1000
  const height = 300
  const left = 44
  const right = 24
  const top = 24
  const bottom = 38
  const chartWidth = width - left - right
  const chartHeight = height - top - bottom
  const maxMinute = Math.max(90, ...points.map((point) => n(point.minute)))
  const maxValue = Math.max(1, ...points.flatMap((point) => [n(point.home), n(point.away)]))

  const xFor = (minute: number) => left + (minute / maxMinute) * chartWidth
  const yFor = (value: number) => top + chartHeight - (value / maxValue) * chartHeight

  const homeLine = points.map((point) => `${xFor(n(point.minute)).toFixed(1)},${yFor(n(point.home)).toFixed(1)}`).join(' ')
  const awayLine = points.map((point) => `${xFor(n(point.minute)).toFixed(1)},${yFor(n(point.away)).toFixed(1)}`).join(' ')

  return (
    <section style={panelStyle({ padding: 18, marginTop: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <h2 style={titleStyle()}>Rolling momentum</h2>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 5 }}>
            Ten minute rolling danger score, with a separate possession view based on the team controlling the event flow through the match.
          </div>
        </div>
        <div style={{ display: 'grid', gap: 8, justifyItems: 'end' }}>
          <div style={{ textAlign: 'right', fontSize: 12, color: 'var(--muted)' }}>
            <div><span style={{ color: 'rgba(45,216,233,0.95)', fontWeight: 900 }}>Home</span> {selected?.home_team}</div>
            <div style={{ marginTop: 3 }}><span style={{ color: 'rgba(167,139,250,0.95)', fontWeight: 900 }}>Away</span> {selected?.away_team}</div>
          </div>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setView('danger')} style={{ ...buttonStyle(view === 'danger'), padding: '8px 10px' }}>Danger</button>
            <button type="button" onClick={() => setView('possession')} style={{ ...buttonStyle(view === 'possession'), padding: '8px 10px' }}>Possession</button>
          </div>
        </div>
      </div>

      {view === 'danger' ? (
        <>
          <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 300, display: 'block' }}>
            <rect x={left} y={top} width={chartWidth} height={chartHeight} rx={14} fill="rgba(255,255,255,0.035)" />
            {[0, 15, 30, 45, 60, 75, 90].map((minute) => (
              <g key={minute}>
                <line x1={xFor(minute)} x2={xFor(minute)} y1={top} y2={top + chartHeight} stroke="rgba(255,255,255,0.06)" />
                <text x={xFor(minute)} y={height - 13} fill="rgba(232,234,240,0.58)" fontSize="11" textAnchor="middle">{minute}'</text>
              </g>
            ))}
            {[0, 0.25, 0.5, 0.75, 1].map((ratio) => (
              <line
                key={ratio}
                x1={left}
                x2={left + chartWidth}
                y1={top + chartHeight - ratio * chartHeight}
                y2={top + chartHeight - ratio * chartHeight}
                stroke="rgba(255,255,255,0.045)"
              />
            ))}

            <polyline points={homeLine} fill="none" stroke="rgba(45,216,233,0.96)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
            <polyline points={awayLine} fill="none" stroke="rgba(167,139,250,0.96)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />

            {markers.map((marker, index) => {
              const markerMinute = n(marker.minute)
              const isHome = s(marker.team_side) === 'home'
              const markerX = xFor(markerMinute)
              const markerY = marker.marker_type === 'red_card' ? top + 24 : isHome ? top + 52 : top + 86
              const fill = marker.marker_type === 'red_card' ? '#ef4444' : isHome ? 'rgba(45,216,233,0.95)' : 'rgba(167,139,250,0.95)'
              return (
                <g key={`${marker.marker_type}-${index}`} {...chartTooltip.bind(`${marker.minute}' ${marker.team} ${marker.player ?? ''} ${marker.score_after_event ?? ''}`)}>
                  {marker.marker_type === 'red_card' ? (
                    <>
                      <rect x={markerX - 10} y={markerY - 13} width="20" height="26" rx="4" fill={fill} />
                      <text x={markerX} y={markerY + 4} fontSize="9" fontWeight="900" textAnchor="middle" fill="#fff">RC</text>
                    </>
                  ) : (
                    <>
                      <circle cx={markerX} cy={markerY} r="13" fill={fill} stroke="rgba(255,255,255,0.7)" strokeWidth="1.5" />
                      <text x={markerX} y={markerY + 4} fontSize="10" fontWeight="900" textAnchor="middle" fill="#07111c">G</text>
                    </>
                  )}
                  <title>{`${marker.minute}' ${marker.team} ${marker.player ?? ''} ${marker.score_after_event ?? ''}`}</title>
                </g>
              )
            })}
            <SvgPitchTooltip tooltip={chartTooltip.tooltip} viewBoxWidth={width} viewBoxHeight={height} />
          </svg>
          <MomentumAnalysisPanel analysis={analysis} />
        </>
      ) : (
        <PossessionMomentumChart analysis={analysis} />
      )}
    </section>
  )
}

function PossessionMomentumChart({ analysis }: { analysis: MatchAnalysisResponse }) {
  const tooltip = useSvgPitchTooltip()
  const points = recordArray((analysis as unknown as AnyRecord).momentum_possession)
  const selected = analysis.selected_fixture
  const width = 1000
  const height = 300
  const left = 44
  const right = 24
  const top = 24
  const bottom = 42
  const chartWidth = width - left - right
  const chartHeight = height - top - bottom
  const maxMinute = Math.max(90, ...points.map((point) => n(point.minute)))
  const xFor = (minute: number) => left + (minute / maxMinute) * chartWidth
  const yForShare = (share: number) => top + chartHeight - (Math.max(0, Math.min(100, share)) / 100) * chartHeight
  const homeShareLine = points.map((point) => `${xFor(n(point.minute)).toFixed(1)},${yForShare(n(point.home_share_pct, 50)).toFixed(1)}`).join(' ')
  const homeName = selected?.home_team ?? 'Home'
  const awayName = selected?.away_team ?? 'Away'
  const barWidth = Math.max(1.4, chartWidth / Math.max(maxMinute, 1))

  if (!points.length) {
    return <div style={{ ...smallInfoStyle(), padding: 16, border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14 }}>No possession timeline is available for this match state.</div>
  }

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 300, display: 'block' }}>
        <rect x={left} y={top} width={chartWidth} height={chartHeight} rx={14} fill="rgba(255,255,255,0.035)" />
        {[0, 15, 30, 45, 60, 75, 90].map((minute) => (
          <g key={minute}>
            <line x1={xFor(minute)} x2={xFor(minute)} y1={top} y2={top + chartHeight} stroke="rgba(255,255,255,0.06)" />
            <text x={xFor(minute)} y={height - 13} fill="rgba(232,234,240,0.58)" fontSize="11" textAnchor="middle">{minute}'</text>
          </g>
        ))}
        {[25, 50, 75].map((share) => (
          <g key={share}>
            <line x1={left} x2={left + chartWidth} y1={yForShare(share)} y2={yForShare(share)} stroke="rgba(255,255,255,0.055)" />
            <text x={left - 8} y={yForShare(share) + 3} fill="rgba(232,234,240,0.56)" fontSize="10" textAnchor="end">{share}%</text>
          </g>
        ))}
        {points.map((point, index) => {
          const minute = n(point.minute)
          const dominant = s(point.dominant_side)
          const fill = dominant === 'home' ? 'rgba(45,216,233,0.70)' : dominant === 'away' ? 'rgba(167,139,250,0.70)' : 'rgba(148,163,184,0.38)'
          const eventCount = n(point.home_events) + n(point.away_events)
          const opacity = eventCount > 0 ? 0.82 : 0.36
          const tip = `${minute}' possession signal. ${homeName} ${n(point.home_share_pct).toFixed(0)} percent, ${awayName} ${n(point.away_share_pct).toFixed(0)} percent. Dominant team: ${s(point.dominant_team)}. Source: ${s(point.signal)}.`
          return (
            <rect
              key={`${minute}-${index}`}
              x={xFor(Math.max(0, minute - 1))}
              y={top}
              width={barWidth}
              height={chartHeight}
              fill={fill}
              opacity={opacity}
              {...tooltip.bind(tip)}
            />
          )
        })}
        <polyline points={homeShareLine} fill="none" stroke="rgba(255,255,255,0.92)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <text x={left + 6} y={top + 14} fill="rgba(45,216,233,0.96)" fontSize="11" fontWeight="900">{homeName}</text>
        <text x={left + 6} y={top + chartHeight - 8} fill="rgba(167,139,250,0.96)" fontSize="11" fontWeight="900">{awayName}</text>
        <SvgPitchTooltip tooltip={tooltip.tooltip} viewBoxWidth={width} viewBoxHeight={height} />
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 11, marginTop: 8 }}>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(45,216,233,0.70)', marginRight: 6, verticalAlign: -1 }} />{homeName} event possession spell</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(167,139,250,0.70)', marginRight: 6, verticalAlign: -1 }} />{awayName} event possession spell</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(148,163,184,0.38)', marginRight: 6, verticalAlign: -1 }} />Balanced or low event signal</span>
      </div>
      <div style={{ ...smallInfoStyle(), marginTop: 8 }}>This is an event based possession timeline using a three minute rolling window, not tracking data.</div>
    </div>
  )
}

function BroadcastStatRow({ label, home, away, homeTeam, awayTeam, decimals = 0 }: { label: string; home: number; away: number; homeTeam: string; awayTeam: string; decimals?: number }) {
  const maxValue = Math.max(home, away, 1)
  const homePct = Math.max(3, Math.min(100, (home / maxValue) * 100))
  const awayPct = Math.max(3, Math.min(100, (away / maxValue) * 100))
  const formatValue = (value: number) => decimals > 0 ? value.toFixed(decimals) : Math.round(value).toString()

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '72px 1fr 118px 1fr 72px', gap: 10, alignItems: 'center' }}>
      <div style={{ textAlign: 'right', fontSize: 14, fontWeight: 950, color: 'rgba(45,216,233,0.96)' }}>{formatValue(home)}</div>
      <div style={{ height: 12, borderRadius: 999, background: 'rgba(255,255,255,0.07)', overflow: 'hidden', transform: 'scaleX(-1)' }} title={homeTeam}>
        <div style={{ width: `${homePct}%`, height: '100%', borderRadius: 999, background: 'linear-gradient(90deg, rgba(45,216,233,0.30), rgba(45,216,233,0.96))' }} />
      </div>
      <div style={{ textAlign: 'center' }}>
        <div style={{ color: 'var(--text)', fontSize: 12, fontWeight: 900 }}>{label}</div>
      </div>
      <div style={{ height: 12, borderRadius: 999, background: 'rgba(255,255,255,0.07)', overflow: 'hidden' }} title={awayTeam}>
        <div style={{ width: `${awayPct}%`, height: '100%', borderRadius: 999, background: 'linear-gradient(90deg, rgba(167,139,250,0.96), rgba(167,139,250,0.30))' }} />
      </div>
      <div style={{ textAlign: 'left', fontSize: 14, fontWeight: 950, color: 'rgba(167,139,250,0.96)' }}>{formatValue(away)}</div>
    </div>
  )
}

function SummaryPanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const home = analysis.team_summaries?.home as AnyRecord | undefined
  const away = analysis.team_summaries?.away as AnyRecord | undefined
  const fixture = analysis.selected_fixture
  const homeTeam = s(home?.team, fixture?.home_team ?? 'Home')
  const awayTeam = s(away?.team, fixture?.away_team ?? 'Away')
  const stats = [
    { label: 'Shots', key: 'shots' },
    { label: 'Shots on target', key: 'shots_on_target' },
    { label: 'xG', key: 'xg', decimals: 2 },
    { label: 'Take ons', key: 'take_ons' },
    { label: 'Successful take ons', key: 'successful_take_ons' },
    { label: 'Inferred carries', key: 'inferred_carries' },
    { label: 'Progressive carries', key: 'progressive_carries' },
    { label: 'Box entries', key: 'box_entries' },
    { label: 'Final third entries', key: 'final_third_entries' },
    { label: 'Set pieces', key: 'set_piece_actions' },
    { label: 'Corners', key: 'corners' },
    { label: 'Free kicks', key: 'free_kicks' },
    { label: 'Fouls', key: 'fouls' },
    { label: 'Cards', key: 'cards' },
    { label: 'Interceptions', key: 'interceptions' },
    { label: 'Defensive actions', key: 'defensive_actions' },
  ]

  return (
    <section style={panelStyle({ padding: 18, marginTop: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <h2 style={titleStyle()}>General summary</h2>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5 }}>
            Broadcast style match comparison. Technical data source details are kept in the audit expander at the bottom.
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 28, fontWeight: 950 }}>{fixture?.home_score ?? ' '} : {fixture?.away_score ?? ' '}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{fixture ? `${fixture.home_team} v ${fixture.away_team} • ${formatKickoff(fixture.kickoff)}` : 'Select a fixture'}</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 14, alignItems: 'center', marginBottom: 14 }}>
        <div style={{ textAlign: 'right', color: 'rgba(45,216,233,0.96)', fontWeight: 950 }}>{homeTeam}</div>
        <div style={{ ...labelStyle(), textAlign: 'center' }}>Match stats</div>
        <div style={{ textAlign: 'left', color: 'rgba(167,139,250,0.96)', fontWeight: 950 }}>{awayTeam}</div>
      </div>

      <div style={{ display: 'grid', gap: 11 }}>
        {stats.map((item) => (
          <BroadcastStatRow
            key={item.key}
            label={item.label}
            home={metric(home, item.key)}
            away={metric(away, item.key)}
            homeTeam={homeTeam}
            awayTeam={awayTeam}
            decimals={item.decimals ?? 0}
          />
        ))}
      </div>
    </section>
  )
}


function DirectionPitchMap({ title, lanes, tone }: { title: string; lanes: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const laneRows: AnyRecord[] = ['left', 'central', 'right'].map((lane) => (
    lanes.find((item) => s(item.lane) === lane) ?? {
      lane,
      label: lane === 'central' ? 'Centre' : lane[0].toUpperCase() + lane.slice(1),
      y_min: lane === 'left' ? 0 : lane === 'central' ? 33.333 : 66.667,
      y_max: lane === 'left' ? 33.333 : lane === 'central' ? 66.667 : 100,
      share_pct: 0,
      count: 0,
    }
  ))
  const mainLane = [...laneRows].sort((a, b) => n(b.share_pct) - n(a.share_pct))[0]

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5 }}>
            Lane share shown on the pitch. Threat mode uses xG and positive xT created.
          </div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{s(mainLane?.label, 'No')} lane</div>
      </div>
      <div style={{ height: 300 }}>
        <PitchCanvas height={300}>
          <PitchLaneLayer lanes={laneRows} tone={tone} />
        </PitchCanvas>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 10 }}>
        {laneRows.map((lane) => (
          <div key={s(lane.lane)} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: '8px 9px', background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 950 }}>{s(lane.label)}</span>
              <span style={{ fontSize: 11, color: tone === 'cyan' ? '#2dd8e9' : '#a78bfa', fontWeight: 950 }}>{n(lane.share_pct).toFixed(1)}%</span>
            </div>
            <div style={{ ...smallInfoStyle(), marginTop: 4 }}>
              xG {n(lane.xg).toFixed(2)} · xT {n(lane.xt_created).toFixed(2)} · {n(lane.shots)} shots
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function HeatGridPitch({ title, heatmap, note, tone = 'amber' }: { title: string; heatmap: AnyRecord; note?: string; tone?: 'cyan' | 'violet' | 'amber' }) {
  const cells = listFromRecord(heatmap, 'cells')
  const points = listFromRecord(heatmap, 'points')

  return (
    <div style={panelStyle({ padding: 14 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start', marginBottom: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, fontWeight: 950 }}>{title}</h3>
          {note && <div style={{ ...smallInfoStyle(), marginTop: 4 }}>{note}</div>}
        </div>
        <div style={labelStyle()}>{cells.length} zones</div>
      </div>
      <PitchCanvas height={260}>
        {cells.length ? <PitchHeatLayer heatmap={heatmap} tone={tone} /> : <EmptyPitchNote label="No heat map data available." />}
        {points.length ? <PitchPointLayer points={points} tone={tone} maxPoints={70} /> : null}
      </PitchCanvas>
      <EventLegend items={['heat_funnel', 'shot', 'goal']} tone={tone} compact align="right" />
    </div>
  )
}

function SequencePitch({ actions }: { actions: AnyRecord[] }) {
  return (
    <div style={{ height: 310 }}>
      <PitchCanvas height={310}>
        {actions.length ? <PitchSequenceLayer actions={actions} tone="cyan" /> : <EmptyPitchNote label="No sequence actions to draw." />}
      </PitchCanvas>
    </div>
  )
}

function GoalSequenceBuilder({ sequences }: { sequences: AnyRecord[] }) {
  const [selectedId, setSelectedId] = useState('')

  useEffect(() => {
    if (!selectedId && sequences.length) {
      setSelectedId(s(sequences[0]?.sequence_id))
    }
  }, [selectedId, sequences])

  const selected = sequences.find((item) => s(item.sequence_id) === selectedId) ?? sequences[0]
  const actions = Array.isArray(selected?.actions) ? selected.actions as AnyRecord[] : []

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Goal and shot sequence builder</h3>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5 }}>
            Chain starts from the shot and walks backwards through same team possession until a break, set piece start, time gap, or max chain limit.
          </div>
        </div>
        <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} style={{ ...FIELD_STYLE, width: 310, marginTop: 0 }}>
          {sequences.map((sequence) => (
            <option key={s(sequence.sequence_id)} value={s(sequence.sequence_id)}>
              {sequence.is_goal ? 'Goal' : 'Shot'} {n(sequence.minute).toFixed(1)}' • {s(sequence.team)} • {s(sequence.player)}
            </option>
          ))}
        </select>
      </div>

      {!selected && <div style={{ color: 'var(--muted)', fontSize: 13 }}>No shots or goals found for this match.</div>}

      {selected && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1.2fr) minmax(280px, 0.8fr)', gap: 16 }}>
          <SequencePitch actions={actions} />
          <div style={{ display: 'grid', gap: 8, alignContent: 'start' }}>
            <div style={{ ...labelStyle(), marginBottom: 2 }}>{s(selected.start_reason, 'Same possession chain')}</div>
            {actions.map((action) => (
              <div key={`${s(action.event_index)}-${s(action.order)}`} style={{ padding: '9px 10px', borderRadius: 12, background: 'rgba(255,255,255,0.045)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ fontSize: 12, fontWeight: 900 }}>{s(action.order)}. {s(action.player) || 'Unknown'} • {s(action.type) || 'Event'}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{s(action.minute)}' • {s(action.outcome_type)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function GoalMouthMap({ title, points, tone }: { title: string; points: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const colour = tone === 'cyan' ? '#2dd8e9' : '#a78bfa'
  const goalX = 72
  const goalY = 56
  const goalW = 216
  const goalH = 72
  const groundY = goalY + goalH
  const outerX = 36
  const outerY = 22
  const outerW = 288
  const outerH = 140
  const visiblePoints = points
  const shots = visiblePoints.length
  const goals = visiblePoints.filter((point) => (point.is_goal)).length
  const onTarget = visiblePoints.filter((point) => point.on_target_plane !== false).length
  const offFrame = Math.max(shots - onTarget, 0)

  const toSvgPoint = (point: AnyRecord) => {
    const rawHorizontal = n(point.goal_mouth_horizontal, n(point.x, n(point.raw_goal_mouth_y)))
    const rawVertical = n(point.goal_mouth_vertical, n(point.y, n(point.raw_goal_mouth_z)))
    const displayHorizontal = n(point.goal_mouth_display_x, n(point.x, rawHorizontal))
    const displayVertical = n(point.goal_mouth_display_y, n(point.y, rawVertical))
    const rawPx = goalX + (displayHorizontal / 100) * goalW
    const rawPy = groundY - (displayVertical / 100) * goalH
    return {
      rawHorizontal,
      rawVertical,
      displayHorizontal,
      displayVertical,
      px: Math.max(outerX + 8, Math.min(outerX + outerW - 8, rawPx)),
      py: Math.max(outerY + 8, Math.min(outerY + outerH - 10, rawPy)),
    }
  }

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5, lineHeight: 1.45, maxWidth: 720 }}>
            FotMob style goal view. Shot target pitch Y is projected against the real goal posts, while height uses goalMouthZ when available. Grey ringed shots are misses shown outside the frame.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          {[
            ['Shots', shots],
            ['Goals', goals],
            ['On frame', onTarget],
            ['Off frame', offFrame],
          ].map(([label, value]) => (
            <div key={String(label)} style={{ padding: '6px 9px', borderRadius: 999, border: '1px solid rgba(255,255,255,0.10)', background: 'rgba(255,255,255,0.045)' }}>
              <span style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 800, textTransform: 'uppercase', letterSpacing: 0.45 }}>{label}</span>
              <span style={{ color: 'var(--text)', fontSize: 12, fontWeight: 950, marginLeft: 6 }}>{value}</span>
            </div>
          ))}
        </div>
      </div>

      <svg viewBox="0 0 360 192" style={{ width: '100%', height: 282, display: 'block', marginTop: 10, overflow: 'visible' }}>
        <defs>
          <linearGradient id={`${tone}-goal-bg`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgba(255,255,255,0.060)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0.015)" />
          </linearGradient>
          <filter id={`${tone}-goal-glow`} x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="0" stdDeviation="1.6" floodColor="rgba(255,255,255,0.45)" />
          </filter>
        </defs>

        <rect x={outerX} y={outerY} width={outerW} height={outerH} rx="14" fill="rgba(255,255,255,0.025)" stroke="rgba(255,255,255,0.11)" strokeWidth="1.4" />
        <rect x={outerX} y={groundY} width={outerW} height={outerY + outerH - groundY} fill="rgba(255,255,255,0.020)" />
        <rect x={goalX} y={goalY} width={goalW} height={goalH} fill={`url(#${tone}-goal-bg)`} stroke="rgba(255,255,255,0.18)" strokeWidth="1" />

        {[0.2, 0.4, 0.6, 0.8].map((share) => (
          <line key={`v-${share}`} x1={goalX + goalW * share} x2={goalX + goalW * share} y1={goalY} y2={groundY} stroke="rgba(255,255,255,0.075)" strokeWidth="1" />
        ))}
        {[0.25, 0.5, 0.75].map((share) => (
          <line key={`h-${share}`} x1={goalX} x2={goalX + goalW} y1={goalY + goalH * share} y2={goalY + goalH * share} stroke="rgba(255,255,255,0.075)" strokeWidth="1" />
        ))}
        {[0.16, 0.32, 0.48, 0.64, 0.8].map((share) => (
          <line key={`diag-l-${share}`} x1={goalX + goalW * share} x2={goalX + goalW * Math.min(1, share + 0.12)} y1={goalY} y2={groundY} stroke="rgba(255,255,255,0.045)" strokeWidth="1" />
        ))}
        {[0.2, 0.36, 0.52, 0.68, 0.84].map((share) => (
          <line key={`diag-r-${share}`} x1={goalX + goalW * share} x2={goalX + goalW * Math.max(0, share - 0.12)} y1={goalY} y2={groundY} stroke="rgba(255,255,255,0.045)" strokeWidth="1" />
        ))}

        <line x1={outerX + 2} x2={outerX + outerW - 2} y1={groundY} y2={groundY} stroke="rgba(255,255,255,0.16)" strokeWidth="1.2" />
        <line x1={goalX} x2={goalX} y1={goalY} y2={groundY} stroke="rgba(235,237,242,0.82)" strokeWidth="4.4" strokeLinecap="round" filter={`url(#${tone}-goal-glow)`} />
        <line x1={goalX + goalW} x2={goalX + goalW} y1={goalY} y2={groundY} stroke="rgba(235,237,242,0.82)" strokeWidth="4.4" strokeLinecap="round" filter={`url(#${tone}-goal-glow)`} />
        <line x1={goalX} x2={goalX + goalW} y1={goalY} y2={goalY} stroke="rgba(235,237,242,0.84)" strokeWidth="4.4" strokeLinecap="round" filter={`url(#${tone}-goal-glow)`} />
        <line x1={outerX + 6} x2={outerX + outerW - 6} y1={groundY} y2={groundY} stroke="rgba(235,237,242,0.42)" strokeWidth="3.2" strokeLinecap="round" />
        <line x1={goalX} x2={goalX + goalW} y1={groundY} y2={groundY} stroke="rgba(235,237,242,0.90)" strokeWidth="5.4" strokeLinecap="round" filter={`url(#${tone}-goal-glow)`} />

        <text x={goalX + goalW / 2} y={goalY - 10} fill="rgba(232,234,240,0.68)" fontSize="8" fontWeight="900" textAnchor="middle">crossbar</text>
        <text x={goalX - 12} y={goalY + goalH / 2} fill="rgba(232,234,240,0.56)" fontSize="8" fontWeight="900" textAnchor="end">left post</text>
        <text x={goalX + goalW + 12} y={goalY + goalH / 2} fill="rgba(232,234,240,0.56)" fontSize="8" fontWeight="900">right post</text>
        <text x={goalX + goalW / 2} y={groundY + 17} fill="rgba(232,234,240,0.68)" fontSize="8" fontWeight="900" textAnchor="middle">goal line and grass</text>
        <text x={outerX + 8} y="181" fill="rgba(232,234,240,0.58)" fontSize="9" fontWeight="900">Shooter left</text>
        <text x={outerX + outerW - 8} y="181" fill="rgba(232,234,240,0.58)" fontSize="9" fontWeight="900" textAnchor="end">Shooter right</text>

        {visiblePoints.map((point, index) => {
          const mapped = toSvgPoint(point)
          const radius = Math.max(3.6, Math.min(8.2, 3.8 + n(point.xg) * 7.2))
          const status = s(point.goal_mouth_status, s(point.zone, 'shot'))
          const onFrame = point.on_target_plane !== false
          const isGoal = (point.is_goal)
          const isWoodwork = (point.is_goal_mouth_woodwork)
          const fill = isGoal ? '#ef4444' : onFrame ? colour : 'rgba(148,163,184,0.72)'
          const stroke = isGoal ? 'rgba(255,255,255,0.95)' : isWoodwork ? '#f59e0b' : onFrame ? 'rgba(255,255,255,0.78)' : 'rgba(255,255,255,0.52)'
          const strokeWidth = isGoal ? 1.5 : isWoodwork ? 1.4 : 1.05
          return (
            <g key={`${index}-${mapped.px}-${mapped.py}`}>
              {!onFrame && <circle cx={mapped.px} cy={mapped.py} r={radius + 3.1} fill="none" stroke="rgba(148,163,184,0.38)" strokeWidth="1" strokeDasharray="2.2 2.2" />}
              {isGoal && <circle cx={mapped.px} cy={mapped.py} r={radius + 4.2} fill="rgba(239,68,68,0.16)" stroke="rgba(255,255,255,0.28)" strokeWidth="0.8" />}
              <circle cx={mapped.px} cy={mapped.py} r={isGoal ? radius + 0.9 : radius} fill={fill} opacity={isGoal ? 0.98 : onFrame ? 0.82 : 0.66} stroke={stroke} strokeWidth={strokeWidth} />
              {isGoal && <circle cx={mapped.px} cy={mapped.py} r="1.35" fill="rgba(255,255,255,0.92)" />}
              <title>{`${s(point.minute)}' ${s(point.player)} ${s(point.outcome_type)} • ${status} • target pitch Y ${n(point.goal_target_pitch_y).toFixed(1)} mapped to net ${mapped.rawHorizontal.toFixed(1)} • height ${mapped.rawVertical.toFixed(1)}${point.goal_mouth_height_estimated ? ' estimated' : ''} • xG ${n(point.xg).toFixed(2)}`}</title>
            </g>
          )
        })}
      </svg>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 11, marginTop: 8 }}>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: '#ef4444', border: '1px solid rgba(255,255,255,0.8)', marginRight: 6, verticalAlign: -1 }} />Goal</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: colour, border: '1px solid rgba(255,255,255,0.7)', marginRight: 6, verticalAlign: -1 }} />On frame</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(148,163,184,0.72)', border: '1px dashed rgba(255,255,255,0.55)', marginRight: 6, verticalAlign: -1 }} />Missed frame</span>
        <span style={{ color: 'rgba(232,234,240,0.64)' }}>White frame is the actual goal mouth. Horizontal position is converted from pitch target Y, not raw pitch X/Y.</span>
      </div>
    </div>
  )
}


function RecentPatternCard({ title, pattern }: { title: string; pattern: AnyRecord }) {
  const available = Boolean(pattern.available)
  const selected = objectFromRecord(pattern, 'selected_context')
  const selectedFallback = objectFromRecord(pattern, 'selected_match')
  const selectedMatch = Object.keys(selected).length ? selected : selectedFallback
  const average = objectFromRecord(pattern, 'recent_average')
  const recentMatches = listFromRecord(pattern, 'recent_matches')
  const keys = ['shots', 'final_third_entries', 'box_entries', 'crosses', 'goals', 'red_cards_for']
  const contextItems = [
    ['Opponent', s(selectedMatch.opponent, 'Unknown')],
    ['Venue', s(selectedMatch.venue, 'Unknown')],
    ['Score', s(selectedMatch.scoreline, 'N/A')],
    ['Possession', selectedMatch.possession_pct === null || selectedMatch.possession_pct === undefined ? 'N/A' : `${n(selectedMatch.possession_pct).toFixed(1)}%`],
    ['Red cards', `${n(selectedMatch.red_cards_for)} for • ${n(selectedMatch.opponent_red_cards)} against`],
    ['Opponent shots', selectedMatch.opponent_shots === null || selectedMatch.opponent_shots === undefined ? 'N/A' : `${n(selectedMatch.opponent_shots)}`],
  ]

  return (
    <div style={panelStyle({ padding: 16, minWidth: 0, overflow: 'hidden' })}>
      <h3 style={titleStyle()}>{title}</h3>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5 }}>
        {available ? `Compared against previous ${n(pattern.match_count)} matches before this fixture.` : s(pattern.reason, 'Build the processed store to enable recent match context.')}
      </div>
      {available && (
        <div style={{ display: 'grid', gap: 14, marginTop: 14, minWidth: 0 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 8, minWidth: 0 }}>
            {contextItems.map(([label, value]) => (
              <div key={label} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 13, padding: 10, background: 'rgba(255,255,255,0.035)', minWidth: 0 }}>
                <div style={labelStyle()}>{label}</div>
                <div style={{ marginTop: 5, fontSize: 13, fontWeight: 950, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gap: 9, minWidth: 0 }}>
            {keys.map((key) => (
              <div key={key} style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12, minWidth: 0 }}>
                  <span style={{ color: 'var(--muted)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{key.replaceAll('_', ' ')}</span>
                  <span style={{ whiteSpace: 'nowrap' }}>{n(selectedMatch[key])} this match • {n(average[key]).toFixed(1)} recent avg</span>
                </div>
                <div style={{ marginTop: 5, minWidth: 0 }}>
                  <CompareBar home={n(selectedMatch[key])} away={n(average[key])} homeLabel="match" awayLabel="avg" />
                </div>
              </div>
            ))}
          </div>

          <div style={{ minWidth: 0 }}>
            <div style={{ ...labelStyle(), marginBottom: 8 }}>Previous match context</div>
            <div style={{ overflowX: 'auto', maxWidth: '100%' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead>
                  <tr>
                    {['Date', 'Venue', 'Opponent', 'Score', 'Poss', 'RC', 'Shots', 'Opp shots', 'Final third', 'Box entries'].map((header) => (
                      <th key={header} style={{ textAlign: 'left', padding: '8px 7px', fontSize: 11, color: 'var(--muted)', borderBottom: '1px solid rgba(255,255,255,0.1)', whiteSpace: 'nowrap' }}>{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recentMatches.map((match, index) => (
                    <tr key={`${s(match.match_id)}-${index}`}>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{s(match.match_date).slice(0, 10) || 'N/A'}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{s(match.venue, 'N/A')}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, fontWeight: 850, whiteSpace: 'nowrap' }}>{s(match.opponent, 'Unknown')}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{s(match.scoreline, 'N/A')}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{match.possession_pct === null || match.possession_pct === undefined ? 'N/A' : `${n(match.possession_pct).toFixed(1)}%`}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{n(match.red_cards_for)} / {n(match.opponent_red_cards)}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{n(match.shots)}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{match.opponent_shots === null || match.opponent_shots === undefined ? 'N/A' : n(match.opponent_shots)}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{n(match.final_third_entries)}</td>
                      <td style={{ padding: '8px 7px', fontSize: 12, whiteSpace: 'nowrap' }}>{n(match.box_entries)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ ...smallInfoStyle(), marginTop: 8 }}>{s(pattern.note, 'Possession is estimated from event pass share where provider possession is unavailable.')}</div>
          </div>
        </div>
      )}
    </div>
  )
}

function RecentContextTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [side, setSide] = useState<Side>('home')
  const patterns = analysis.recent_patterns ?? { home: { available: false }, away: { available: false } }
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'
  const selectedName = side === 'home' ? homeName : awayName

  return (
    <div style={{ display: 'grid', gap: 14, minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <h3 style={{ ...titleStyle(), fontSize: 18 }}>Recent attacking context</h3>
          <div style={smallInfoStyle()}>One team is shown at a time so the card, comparison bars and previous match table have the full page width.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setSide('home')} style={buttonStyle(side === 'home')}>{homeName}</button>
          <button type="button" onClick={() => setSide('away')} style={buttonStyle(side === 'away')}>{awayName}</button>
        </div>
      </div>
      <RecentPatternCard title={`${selectedName} recent attacking context`} pattern={patterns[side] as AnyRecord} />
    </div>
  )
}

function PhaseSummaryList({ title, items }: { title: string; items: AnyRecord[] }) {
  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>{title}</h3>
      <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
        {items.map((item, index) => (
          <div key={`${s(item.title)}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 12, background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 13, fontWeight: 900 }}>{s(item.title)}</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5, lineHeight: 1.45 }}>{s(item.summary)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function XTPossessionValuePanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [showHeatFunnel, setShowHeatFunnel] = useState(false)
  const xt = (analysis.xt_analysis ?? {}) as Record<Side | string, AnyRecord>
  const home = (xt.home ?? {}) as AnyRecord
  const away = (xt.away ?? {}) as AnyRecord
  const homeName = s(home.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayName = s(away.team, analysis.selected_fixture?.away_team ?? 'Away')
  const homeActions = listFromRecord(home, 'top_actions')
  const awayActions = listFromRecord(away, 'top_actions')
  const homePlayers = listFromRecord(home, 'top_players')
  const awayPlayers = listFromRecord(away, 'top_players')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Expected Threat and possession value</h3>
          <div style={smallInfoStyle()}>Values successful open play passes, crosses and carries by how much they moved the ball into more dangerous zones.</div>
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 6 }}>
          <div style={{ ...labelStyle(), color: 'var(--accent)' }}>Progression value, not shot quality</div>
          <HeatFunnelToggle checked={showHeatFunnel} onChange={setShowHeatFunnel} />
        </div>
      </div>

      {(!home.available && !away.available) && <div style={smallInfoStyle()}>{s(xt.note, 'xT is not available for this match sample.')}</div>}

      {(home.available || away.available) && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))', gap: 14 }}>
          {[
            { side: 'home' as Side, team: homeName, data: home, actions: homeActions, players: homePlayers, tone: 'cyan' as const },
            { side: 'away' as Side, team: awayName, data: away, actions: awayActions, players: awayPlayers, tone: 'violet' as const },
          ].map((block) => {
            const heatmap = buildPitchHeatmap(block.actions, 'end', 'xt_added')
            return (
              <div key={block.side} style={panelStyle({ padding: 14, boxShadow: 'none' })}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
                  <div>
                    <h4 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>{block.team}</h4>
                    <div style={smallInfoStyle()}>Total positive xT {n(block.data.total_xt).toFixed(3)}</div>
                  </div>
                  <div style={{ ...labelStyle(), color: block.tone === 'cyan' ? 'rgba(45,216,233,0.96)' : 'rgba(167,139,250,0.96)' }}>{block.actions.length} actions</div>
                </div>
                <div style={{ height: 260 }}>
                  <PitchCanvas height={260}>
                    {block.actions.length ? (
                      <>
                        {showHeatFunnel && <PitchHeatLayer heatmap={heatmap} tone={block.tone} />}
                        <PitchArrowLayer arrows={block.actions} tone={block.tone} maxArrows={28} linkedKey="xt_added" />
                      </>
                    ) : <EmptyPitchNote label="No xT actions to draw." />}
                  </PitchCanvas>
                </div>
                <EventLegend tone={block.tone} items={showHeatFunnel ? ['pass', 'cross', 'carry_path', 'heat_funnel'] : ['pass', 'cross', 'carry_path']} />
                <div style={{ display: 'grid', gap: 7, marginTop: 10 }}>
                  {block.players.slice(0, 4).map((player) => (
                    <div key={s(player.player)} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, padding: '7px 8px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
                      <span>{s(player.player, 'Unknown')}</span>
                      <strong>{n(player.total_xt).toFixed(3)} xT</strong>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function isCarryOrTakeOnEvent(event: AnyRecord): boolean {
  const text = `${s(event.event_kind)} ${s(event.event_type)} ${s(event.type)} ${s(event.label)}`.toLowerCase().replace(/[^a-z0-9]+/g, '')
  return b(event.is_take_on)
    || b(event.is_provider_take_on)
    || b(event.is_inferred_carry)
    || b(event.is_carry)
    || text.includes('takeon')
    || text.includes('dribble')
    || text.includes('inferredcarry')
    || text.includes('carry')
}

function BallCarryingMap({ title, events, tone }: { title: string; events: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const [showHeatFunnel, setShowHeatFunnel] = useState(false)
  const visible = events.filter(isCarryOrTakeOnEvent)
  const heatmap = buildPitchHeatmap(visible, 'end')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={smallInfoStyle()}>Take ons show start and end. Carries remain inferred.</div>
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 4 }}>
          <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{visible.length} actions</div>
          <HeatFunnelToggle checked={showHeatFunnel} onChange={setShowHeatFunnel} />
          <EventLegend
            tone={tone}
            items={showHeatFunnel
              ? ['take_on_path', 'take_on_start', 'take_on_end', 'carry_path', 'carry_end', 'heat_funnel']
              : ['take_on_path', 'take_on_start', 'take_on_end', 'carry_path', 'carry_end']}
          />
        </div>
      </div>
      <PitchCanvas height={320}>
        {visible.length ? (
          <>
            {showHeatFunnel && <PitchHeatLayer heatmap={heatmap} tone={tone} />}
            <PitchArrowLayer arrows={visible} tone={tone} maxArrows={220} />
            <PitchPointLayer points={visible} tone={tone} maxPoints={220} />
          </>
        ) : <EmptyPitchNote label="No take ons or inferred carries." />}
      </PitchCanvas>
    </div>
  )
}



type FinalThirdPassClass = 'goal_chain' | 'shot_chain' | 'carry_into_danger' | 'take_on_in_danger' | 'box_entry' | 'backward_recycle' | 'incomplete' | 'completed'

const FINAL_THIRD_PASS_COLOURS: Record<FinalThirdPassClass, string> = {
  goal_chain: '#fb7185',
  shot_chain: '#f59e0b',
  carry_into_danger: '#22c55e',
  take_on_in_danger: '#38bdf8',
  box_entry: '#a78bfa',
  backward_recycle: '#94a3b8',
  incomplete: '#64748b',
  completed: '#e2e8f0',
}

function finalThirdPassClass(event: AnyRecord): FinalThirdPassClass {
  const raw = s(event.outcome_class, 'completed') as FinalThirdPassClass
  return raw in FINAL_THIRD_PASS_COLOURS ? raw : 'completed'
}

function finalThirdPassColour(event: AnyRecord): string {
  return FINAL_THIRD_PASS_COLOURS[finalThirdPassClass(event)]
}

function FinalThirdPassLegend() {
  const items: Array<{ key: FinalThirdPassClass; label: string }> = [
    { key: 'goal_chain', label: 'Goal chain' },
    { key: 'shot_chain', label: 'Shot chain' },
    { key: 'carry_into_danger', label: 'Carry into danger' },
    { key: 'take_on_in_danger', label: 'Take on in danger' },
    { key: 'box_entry', label: 'Penalty box entry' },
    { key: 'backward_recycle', label: 'Backwards recycle' },
    { key: 'incomplete', label: 'Incomplete' },
  ]
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end', marginTop: 6 }}>
      {items.map((item) => (
        <span key={item.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--muted)' }}>
          <span style={{ width: 18, height: 3, borderRadius: 999, background: FINAL_THIRD_PASS_COLOURS[item.key], display: 'inline-block' }} />
          {item.label}
        </span>
      ))}
    </div>
  )
}

function FinalThirdPassLayer({ passes }: { passes: AnyRecord[] }) {
  return (
    <g>
      <rect x={70} y={2} width={33} height={64} fill="rgba(45,216,233,0.045)" stroke="rgba(45,216,233,0.14)" strokeWidth="0.35" />
      <text x={72} y={6.5} fill="rgba(232,234,240,0.55)" fontSize="3" fontWeight="850">final third</text>
      {passes.slice(0, 180).map((event, index) => {
        const startX = pctCoord(event.x)
        const startY = pctCoord(event.y)
        const endX = pctCoord(event.end_x)
        const endY = pctCoord(event.end_y)
        if (startX === null || startY === null || endX === null || endY === null) return null
        const sx = pitchX(startX)
        const sy = pitchY(startY)
        const ex = pitchX(endX)
        const ey = pitchY(endY)
        const colour = finalThirdPassColour(event)
        const cls = finalThirdPassClass(event)
        const width = cls === 'goal_chain' ? 1.35 : cls === 'shot_chain' ? 1.1 : cls === 'incomplete' ? 0.72 : 0.86
        const dash = cls === 'incomplete' ? '1.2 1.2' : cls === 'backward_recycle' ? '2 1.1' : ''
        const opacity = cls === 'completed' ? 0.52 : 0.86
        return (
          <g key={`${s(event.event_index, String(index))}-${index}`}>
            <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={colour} strokeWidth={width} strokeLinecap="round" strokeDasharray={dash} opacity={opacity} />
            <circle cx={sx} cy={sy} r="0.65" fill="rgba(15,23,42,0.95)" stroke={colour} strokeWidth="0.38" opacity="0.9" />
            <circle cx={ex} cy={ey} r={cls === 'goal_chain' || cls === 'shot_chain' ? 1.05 : 0.78} fill={colour} opacity={opacity} stroke="rgba(255,255,255,0.5)" strokeWidth="0.24" />
            <title>{`${minuteText(event.minute)} ${s(event.player, 'Unknown')} ${s(event.outcome_label, 'Final third pass')}${s(event.next_action_label) ? ` → ${s(event.next_action_label)}` : ''}`}</title>
          </g>
        )
      })}
    </g>
  )
}

function FinalThirdPassMap({ title, passes }: { title: string; passes: AnyRecord[] }) {
  const [penaltyBoxOnly, setPenaltyBoxOnly] = useState(false)
  const [excludeSetPlays, setExcludeSetPlays] = useState(true)
  const visible = passes.filter((event) => {
    if (excludeSetPlays && b(event.is_set_piece)) return false
    if (penaltyBoxOnly) {
      return b(event.is_box_entry) || b(event.ended_in_danger) || b(event.led_to_carry_danger) || b(event.led_to_take_on_danger) || b(event.led_to_shot) || b(event.led_to_goal)
    }
    return true
  })
  const counts = visible.reduce<Record<string, number>>((acc, event) => {
    const key = finalThirdPassClass(event)
    acc[key] = (acc[key] ?? 0) + 1
    return acc
  }, {})

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={smallInfoStyle()}>Passes starting or ending in the final third, coloured by the next useful outcome in the chain.</div>
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 7 }}>
          <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{visible.length} passes</div>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 11, color: 'var(--muted)', cursor: 'pointer' }}>
            <input type="checkbox" checked={penaltyBoxOnly} onChange={(event) => setPenaltyBoxOnly(event.target.checked)} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
            Penalty box and danger only
          </label>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 11, color: 'var(--muted)', cursor: 'pointer' }}>
            <input type="checkbox" checked={excludeSetPlays} onChange={(event) => setExcludeSetPlays(event.target.checked)} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
            Exclude set plays
          </label>
        </div>
      </div>
      <PitchCanvas height={330}>
        {visible.length ? <FinalThirdPassLayer passes={visible} /> : <EmptyPitchNote label="No final third passes for this filter." />}
      </PitchCanvas>
      <FinalThirdPassLegend />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
        {Object.entries(counts).filter(([, count]) => count > 0).map(([key, count]) => (
          <span key={key} style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>
            <span style={{ color: FINAL_THIRD_PASS_COLOURS[key as FinalThirdPassClass], fontWeight: 950 }}>{count}</span> {key.replaceAll('_', ' ')}
          </span>
        ))}
      </div>
    </div>
  )
}

function shotTargetClass(point: AnyRecord): 'goal' | 'on_target' | 'off_target' {
  if (b(point.is_goal)) return 'goal'
  if (point.on_target_plane !== false) return 'on_target'
  return 'off_target'
}

function shotTargetColour(point: AnyRecord): string {
  const cls = shotTargetClass(point)
  if (cls === 'goal') return '#fb7185'
  if (cls === 'on_target') return '#22d3ee'
  return '#94a3b8'
}

function ShotTargetPitchLayer({ points }: { points: AnyRecord[] }) {
  return (
    <g>
      {points.slice(0, 120).map((point, index) => {
        const shotX = pctCoord(point.pitch_x ?? point.x)
        const shotY = pctCoord(point.pitch_y ?? point.y)
        if (shotX === null || shotY === null) return null
        const sx = pitchX(shotX)
        const sy = pitchY(shotY)
        const colour = shotTargetColour(point)
        const cls = shotTargetClass(point)
        const xg = Math.max(0, Math.min(1, n(point.xg, 0.06)))
        const radius = cls === 'goal' ? 2.25 : cls === 'on_target' ? 1.55 + xg * 3.0 : 1.25 + xg * 2.4
        return (
          <g key={`${s(point.event_index, String(index))}-${index}`}>
            {cls === 'goal' ? (
              <>
                <circle cx={sx} cy={sy} r={radius + 1.0} fill="rgba(251,113,133,0.16)" stroke="rgba(251,113,133,0.85)" strokeWidth="0.45" />
                <text x={sx} y={sy + 1.55} fontSize="5.4" fontWeight="950" textAnchor="middle">⚽</text>
              </>
            ) : (
              <circle cx={sx} cy={sy} r={radius} fill={colour} opacity={cls === 'off_target' ? 0.58 : 0.86} stroke="rgba(255,255,255,0.58)" strokeWidth="0.32" />
            )}
            {cls === 'on_target' && <circle cx={sx} cy={sy} r={radius + 1.0} fill="none" stroke="rgba(34,211,238,0.55)" strokeWidth="0.32" />}
            <title>{`${minuteText(point.minute)} ${s(point.player, 'Unknown')} ${s(point.outcome_type)} | xG ${n(point.xg).toFixed(2)}`}</title>
          </g>
        )
      })}
    </g>
  )
}

function GoalShotPitchMap({ title, points, tone }: { title: string; points: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const [shotContactFilter, setShotContactFilter] = useState<ShotContactFilter>('all')
  const coordinateReady = points.filter((point) => pctCoord(point.pitch_x ?? point.x) !== null && pctCoord(point.pitch_y ?? point.y) !== null)
  const visible = coordinateReady.filter((point) => shotContactFilter === 'all' || shotContactType(point) === shotContactFilter)
  const goals = visible.filter((point) => b(point.is_goal)).length
  const onTarget = visible.filter((point) => point.on_target_plane !== false).length
  const offTarget = Math.max(visible.length - onTarget, 0)
  const headedShots = coordinateReady.filter((point) => shotContactType(point) === 'headed').length
  const groundShots = Math.max(coordinateReady.length - headedShots, 0)
  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={smallInfoStyle()}>Clean pitch shot map. Marker size reflects xG and the filter separates ground shots from headed shots.</div>
        </div>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', justifyContent: 'flex-end', alignItems: 'center' }}>
          <select value={shotContactFilter} onChange={(event) => setShotContactFilter(event.currentTarget.value as ShotContactFilter)} style={{ ...FIELD_STYLE, width: 150, marginTop: 0 }}>
            <option value="all">All shots</option>
            <option value="ground">Ground shots</option>
            <option value="headed">Headed shots</option>
          </select>
          <span style={{ ...labelStyle(), color: tone === 'cyan' ? '#2dd8e9' : '#a78bfa' }}>{visible.length} shots</span>
          <span style={{ ...labelStyle(), color: '#fb7185' }}>{goals} goals</span>
          <span style={{ ...labelStyle(), color: '#22d3ee' }}>{onTarget} on target</span>
          <span style={labelStyle()}>{offTarget} off target</span>
        </div>
      </div>
      <PitchCanvas height={330}>
        {visible.length ? <ShotTargetPitchLayer points={visible} /> : <EmptyPitchNote label="No shots match this contact filter." />}
      </PitchCanvas>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 9, justifyContent: 'flex-end', marginTop: 6 }}>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}><span style={{ color: '#fb7185', fontWeight: 950 }}>●</span> Goal</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}><span style={{ color: '#22d3ee', fontWeight: 950 }}>●</span> On target</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}><span style={{ color: '#94a3b8', fontWeight: 950 }}>●</span> Off target or blocked</span>
        <span style={{ fontSize: 10, color: 'var(--muted)' }}>{groundShots} ground · {headedShots} headed</span>
      </div>
    </div>
  )
}

function ThreatBoxPitchMap({ title, data, tone }: { title: string; data: AnyRecord; tone: 'cyan' | 'violet' }) {
  const cells = listFromRecord(data, 'cells')
  const topBoxes = listFromRecord(data, 'top_boxes')
  const totalThreat = n(data.total_threat)
  const activeCells = cells.filter((cell) => n(cell.total_threat, n(cell.value)) > 0)
  const heatmap = {
    x_bins: n(data.x_bins, 7),
    y_bins: n(data.y_bins, 3),
    cells: cells.map((cell) => ({
      ...cell,
      value: n(cell.total_threat, n(cell.value)),
      count: n(cell.action_count, n(cell.count)),
    })),
  }

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>21 box map using positive xT plus shot xG assigned to the chance origin.</div>
        </div>
        <div style={{ ...labelStyle(), color: tone === 'cyan' ? '#2dd8e9' : '#a78bfa' }}>{totalThreat.toFixed(2)} threat</div>
      </div>
      <PitchCanvas height={315}>
        {activeCells.length ? <PitchHeatLayer heatmap={heatmap} tone={tone} /> : <EmptyPitchNote label="No threat boxes available." />}
      </PitchCanvas>
      <EventLegend tone={tone} items={['heat_funnel']} compact align="right" />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginTop: 10 }}>
        {(topBoxes.length ? topBoxes : activeCells.slice(0, 5)).map((box, index) => (
          <div key={`${s(box.box_id, String(index))}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: '8px 9px', background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 950 }}>{s(box.label, `Box ${index + 1}`)}</span>
              <span style={{ fontSize: 11, fontWeight: 950, color: tone === 'cyan' ? '#2dd8e9' : '#a78bfa' }}>{n(box.total_threat, n(box.value)).toFixed(2)}</span>
            </div>
            <div style={{ ...smallInfoStyle(), marginTop: 4 }}>xT {n(box.xt_created).toFixed(2)} · xG {n(box.attributed_xg).toFixed(2)} · {n(box.action_count, n(box.count))} actions</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function passNetworkArrowPath(sx: number, sy: number, ex: number, ey: number, size: number) {
  const angle = Math.atan2(ey - sy, ex - sx)
  const left = angle + Math.PI * 0.82
  const right = angle - Math.PI * 0.82
  return `M${ex},${ey} L${ex + Math.cos(left) * size},${ey + Math.sin(left) * size} L${ex + Math.cos(right) * size},${ey + Math.sin(right) * size} Z`
}

type PassNetworkFocusPoint = {
  key: string
  player: string
  role: 'made' | 'received'
  xSum: number
  ySum: number
  weight: number
}

type PassNetworkSelectOption = {
  value: string
  label: string
  count: number
}

function passNetworkPlayerKey(value: unknown): string {
  return s(value).trim().toLowerCase()
}

function buildPassNetworkPlayerMap(players: AnyRecord[]) {
  const map = new Map<string, AnyRecord>()
  players.forEach((player) => {
    const key = passNetworkPlayerKey(player.player)
    if (key) map.set(key, player)
  })
  return map
}

function passNetworkPointFromPlayer(playerMap: Map<string, AnyRecord>, playerName: unknown, fallbackX: unknown, fallbackY: unknown) {
  const player = playerMap.get(passNetworkPlayerKey(playerName))
  const playerX = pctCoord(player?.avg_x)
  const playerY = pctCoord(player?.avg_y)
  const fallbackPointX = pctCoord(fallbackX)
  const fallbackPointY = pctCoord(fallbackY)

  if (playerX !== null && playerY !== null) {
    return {
      x: playerX,
      y: playerY,
    }
  }

  return {
    x: fallbackPointX ?? 50,
    y: fallbackPointY ?? 50,
  }
}

function addPassNetworkFocusPoint(map: Map<string, PassNetworkFocusPoint>, player: string, role: 'made' | 'received', x: number, y: number, weight: number) {
  const key = `${player}::${role}`
  const current = map.get(key) ?? { key, player, role, xSum: 0, ySum: 0, weight: 0 }
  current.xSum += x * weight
  current.ySum += y * weight
  current.weight += weight
  map.set(key, current)
}

function buildPasserOptions(connections: AnyRecord[]): PassNetworkSelectOption[] {
  const options = new Map<string, PassNetworkSelectOption>()

  connections.forEach((connection) => {
    const passer = s(connection.passer).trim()
    if (!passer) return

    const current = options.get(passer) ?? { value: passer, label: passer, count: 0 }
    current.count += n(connection.count)
    options.set(passer, current)
  })

  return Array.from(options.values()).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function buildReceiverOptions(connections: AnyRecord[], selectedPasser: string): PassNetworkSelectOption[] {
  const options = new Map<string, PassNetworkSelectOption>()

  connections.forEach((connection) => {
    if (selectedPasser && s(connection.passer).trim() !== selectedPasser) return

    const receiver = s(connection.receiver).trim()
    if (!receiver) return

    const current = options.get(receiver) ?? { value: receiver, label: receiver, count: 0 }
    current.count += n(connection.count)
    options.set(receiver, current)
  })

  return Array.from(options.values()).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function buildPassNetworkPlayerOptions(players: AnyRecord[]): PassNetworkSelectOption[] {
  return players
    .map((player) => {
      const label = s(player.player).trim()
      return {
        value: passNetworkPlayerKey(label),
        label,
        count: n(player.passes_involved),
      }
    })
    .filter((option) => option.value && option.label)
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label))
}

function passNetworkConnectionIsVisible(connection: AnyRecord, hiddenPlayerKeys?: Set<string>) {
  const hiddenPlayerKeySet = hiddenPlayerKeys ?? new Set<string>()
  if (!hiddenPlayerKeySet.size) return true
  const passerKey = passNetworkPlayerKey(connection.passer)
  const receiverKey = passNetworkPlayerKey(connection.receiver)
  return !hiddenPlayerKeySet.has(passerKey) && !hiddenPlayerKeySet.has(receiverKey)
}

function passNetworkPlayerIsSubstitute(player: AnyRecord | undefined): boolean {
  if (!player) return false
  return b(player.is_substitute) || b(player.is_subbed_in) || s(player.player_status).toLowerCase().includes('sub')
}

function passNetworkPlayerColour(player: AnyRecord | undefined, primary: string, soft: string) {
  const isSubstitute = passNetworkPlayerIsSubstitute(player)
  return {
    isSubstitute,
    fill: isSubstitute ? '#f59e0b' : primary,
    halo: isSubstitute ? 'rgba(245,158,11,0.22)' : soft,
    stroke: isSubstitute ? 'rgba(251,191,36,0.95)' : 'rgba(255,255,255,0.70)',
  }
}


function PassNetworkPitch({
  network,
  focusConnections,
  tone,
  hiddenPlayerKeys,
}: {
  network: AnyRecord
  focusConnections: AnyRecord[]
  tone: 'cyan' | 'violet'
  hiddenPlayerKeys?: Set<string>
}) {
  const pitchTooltip = useSvgPitchTooltip()
  const hiddenPlayerKeySet = hiddenPlayerKeys ?? new Set<string>()
  const allPlayers = recordArray(network.players)
  const allConnections = recordArray(network.connections)
  const players = allPlayers.filter((player) => !hiddenPlayerKeySet.has(passNetworkPlayerKey(player.player)))
  const connections = allConnections.filter((connection) => passNetworkConnectionIsVisible(connection, hiddenPlayerKeySet))
  const filteredFocusConnections = focusConnections.filter((connection) => passNetworkConnectionIsVisible(connection, hiddenPlayerKeySet))
  const playerMap = buildPassNetworkPlayerMap(players)
  const focusMode = filteredFocusConnections.length > 0
  const visibleConnections = focusMode ? filteredFocusConnections : connections
  const maxConnectionCount = Math.max(1, ...visibleConnections.map((item) => n(item.count)))
  const maxPlayerInvolvement = Math.max(1, ...players.map((player) => n(player.passes_involved)))
  const primary = tone === 'cyan' ? '#2dd8e9' : '#a78bfa'
  const soft = tone === 'cyan' ? 'rgba(45,216,233,0.20)' : 'rgba(167,139,250,0.20)'
  const focusLine = '#f59e0b'

  const focusMap = new Map<string, PassNetworkFocusPoint>()
  filteredFocusConnections.forEach((connection) => {
    const weight = Math.max(1, n(connection.count))
    const startX = pctCoord(connection.avg_start_x)
    const startY = pctCoord(connection.avg_start_y)
    const receiveX = pctCoord(connection.avg_receive_x)
    const receiveY = pctCoord(connection.avg_receive_y)

    if (startX !== null && startY !== null) {
      addPassNetworkFocusPoint(focusMap, s(connection.passer), 'made', startX, startY, weight)
    }

    if (receiveX !== null && receiveY !== null) {
      addPassNetworkFocusPoint(focusMap, s(connection.receiver), 'received', receiveX, receiveY, weight)
    }
  })
  const focusPoints = Array.from(focusMap.values()).filter((item) => item.weight > 0)
  const maxFocusWeight = Math.max(1, ...focusPoints.map((point) => point.weight))

  return (
    <PitchCanvas height={338}>
      {visibleConnections.length ? (
        <g>
          <g>
            {visibleConnections.map((connection, index) => {
              const start = focusMode
                ? { x: pctCoord(connection.avg_start_x) ?? 50, y: pctCoord(connection.avg_start_y) ?? 50 }
                : passNetworkPointFromPlayer(playerMap, connection.passer, connection.avg_start_x, connection.avg_start_y)
              const end = focusMode
                ? { x: pctCoord(connection.avg_receive_x) ?? 50, y: pctCoord(connection.avg_receive_y) ?? 50 }
                : passNetworkPointFromPlayer(playerMap, connection.receiver, connection.avg_receive_x, connection.avg_receive_y)

              const sx = pitchX(start.x)
              const sy = pitchY(start.y)
              const ex = pitchX(end.x)
              const ey = pitchY(end.y)
              const strength = Math.max(0, Math.min(1, n(connection.count) / maxConnectionCount))
              const width = focusMode ? 1.1 + Math.sqrt(strength) * 3.2 : 0.65 + Math.sqrt(strength) * 3.05
              const opacity = focusMode ? 0.52 + strength * 0.43 : 0.20 + strength * 0.66
              const stroke = focusMode ? focusLine : primary

              const tooltip = `${s(connection.label)}. ${n(connection.count)} completed passes. xT ${n(connection.total_xt).toFixed(3)}. Progressive ${n(connection.progressive_passes)}.`

              return (
                <g key={`${s(connection.connection_id, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
                  <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={stroke} strokeWidth={width} strokeLinecap="round" opacity={opacity} />
                  {Math.hypot(ex - sx, ey - sy) > 1.2 && <path d={passNetworkArrowPath(sx, sy, ex, ey, focusMode ? 1.95 : 1.45)} fill={stroke} opacity={opacity} />}
                  <title>{`${s(connection.label)}. ${n(connection.count)} completed passes. xT ${n(connection.total_xt).toFixed(3)}. Progressive ${n(connection.progressive_passes)}.`}</title>
                </g>
              )
            })}
          </g>

          {!focusMode && (
            <g>
              {players.map((player, index) => {
                const x = pctCoord(player.avg_x)
                const y = pctCoord(player.avg_y)
                if (x === null || y === null) return null

                const px = pitchX(x)
                const py = pitchY(y)
                const involvement = n(player.passes_involved)
                const radius = Math.max(2.65, 0.5 + Math.sqrt(Math.max(0, involvement) / maxPlayerInvolvement) * 3.15)
                const colours = passNetworkPlayerColour(player, primary, soft)
                const nodeLabel = getPlayerNodeLabel(player)
                const nameLabel = playerShortName(player.player)
                const shirtText = cleanVisibleText(player.shirt_no) ? `#${cleanVisibleText(player.shirt_no)} ` : ''

                const tooltip = `${shirtText}${s(player.player)}. ${colours.isSubstitute ? 'Substitute. ' : 'Starter. '}${n(player.passes_made)} passes made, ${n(player.passes_received)} received. Network involvement ${involvement}. Average position ${n(player.avg_x).toFixed(1)}, ${n(player.avg_y).toFixed(1)}.`

                return (
                  <g key={`${s(player.player, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
                    <circle cx={px} cy={py} r={radius + 1.25} fill={colours.halo} stroke="rgba(255,255,255,0.20)" strokeWidth="0.45" />
                    <circle cx={px} cy={py} r={radius} fill={colours.fill} opacity="0.88" stroke={colours.stroke} strokeWidth="0.55" />
                    {nodeLabel && <text x={px} y={py + 0.92} textAnchor="middle" fill="rgba(2,6,23,0.96)" fontSize="2.65" fontWeight="950">{nodeLabel}</text>}
                    <text x={px} y={py + radius + 4.2} textAnchor="middle" fill="rgba(232,234,240,0.90)" fontSize="3.15" fontWeight="950">{nameLabel}</text>
                    <title>{tooltip}</title>
                  </g>
                )
              })}
            </g>
          )}

          {focusMode && (
            <g>
              {focusPoints.map((point) => {
                const x = pitchX(point.xSum / point.weight)
                const y = pitchY(point.ySum / point.weight)
                const isMade = point.role === 'made'
                const radius = Math.max(2.45, 0.7 + Math.sqrt(point.weight / maxFocusWeight) * 2.55)
                const player = playerMap.get(passNetworkPlayerKey(point.player))
                const colours = passNetworkPlayerColour(player, primary, soft)
                const fill = colours.isSubstitute ? colours.fill : isMade ? 'rgba(245,158,11,0.94)' : primary
                const halo = colours.isSubstitute ? colours.halo : isMade ? 'rgba(245,158,11,0.22)' : soft
                const nodeLabel = getPlayerNodeLabel(player, point.player)
                const shirtText = cleanVisibleText(player?.shirt_no) ? `#${cleanVisibleText(player?.shirt_no)} ` : ''

                const tooltip = `${shirtText}${point.player}. ${colours.isSubstitute ? 'Substitute. ' : ''}Average position when the pass was ${isMade ? 'made' : 'received'}. ${Math.round(point.weight)} passes.`

                return (
                  <g key={point.key} {...pitchTooltip.bind(tooltip)}>
                    <circle cx={x} cy={y} r={radius + 1.05} fill={halo} stroke="rgba(255,255,255,0.22)" strokeWidth="0.45" />
                    <circle cx={x} cy={y} r={radius} fill={fill} stroke={colours.isSubstitute ? colours.stroke : 'rgba(255,255,255,0.82)'} strokeWidth="0.62" />
                    {nodeLabel && <text x={x} y={y + 0.86} textAnchor="middle" fill="rgba(2,6,23,0.96)" fontSize="2.45" fontWeight="950">{nodeLabel}</text>}
                    <text x={x} y={y + radius + 4.6} textAnchor="middle" fill="rgba(232,234,240,0.92)" fontSize="3.05" fontWeight="950">{playerShortName(point.player)}</text>
                    <title>{tooltip}</title>
                  </g>
                )
              })}
            </g>
          )}
          <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
        </g>
      ) : (
        <EmptyPitchNote label="No completed open play pass connections available for the selected player filter." />
      )}
    </PitchCanvas>
  )
}

function PassNetworkPanel({ title, data, tone }: { title: string; data: AnyRecord; tone: 'cyan' | 'violet' }) {
  const connections = recordArray(data.connections)
  const players = recordArray(data.players)
  const [showSubstitutes, setShowSubstitutes] = useState(false)
  const [manualHiddenPlayerKeys, setManualHiddenPlayerKeys] = useState<string[]>([])
  const manualHiddenPlayerKeySet = useMemo(() => new Set(manualHiddenPlayerKeys), [manualHiddenPlayerKeys])
  const substitutePlayerKeySet = useMemo(
    () => new Set(players.filter((player) => passNetworkPlayerIsSubstitute(player)).map((player) => passNetworkPlayerKey(player.player)).filter(Boolean)),
    [players],
  )
  const effectiveHiddenPlayerKeySet = useMemo(() => {
    const next = new Set(manualHiddenPlayerKeySet)
    if (!showSubstitutes) {
      substitutePlayerKeySet.forEach((key) => next.add(key))
    }
    return next
  }, [manualHiddenPlayerKeySet, showSubstitutes, substitutePlayerKeySet])
  const visibleConnections = useMemo(
    () => connections.filter((connection) => passNetworkConnectionIsVisible(connection, effectiveHiddenPlayerKeySet)),
    [connections, effectiveHiddenPlayerKeySet],
  )
  const visiblePlayers = useMemo(
    () => players.filter((player) => !effectiveHiddenPlayerKeySet.has(passNetworkPlayerKey(player.player))),
    [players, effectiveHiddenPlayerKeySet],
  )
  const [selectedPasser, setSelectedPasser] = useState('')
  const [selectedReceivers, setSelectedReceivers] = useState<string[]>([])
  const playerOptions = useMemo(() => buildPassNetworkPlayerOptions(players), [players])
  const passerOptions = useMemo(() => buildPasserOptions(visibleConnections), [visibleConnections])
  const receiverOptions = useMemo(() => buildReceiverOptions(visibleConnections, selectedPasser), [visibleConnections, selectedPasser])
  const playerOptionKey = playerOptions.map((item) => item.value).join('|')
  const passerOptionKey = passerOptions.map((item) => item.value).join('|')
  const receiverOptionKey = receiverOptions.map((item) => item.value).join('|')

  useEffect(() => {
    const validPlayerKeys = new Set(playerOptions.map((item) => item.value))
    setManualHiddenPlayerKeys((previous) => previous.filter((item) => validPlayerKeys.has(item)))
  }, [playerOptionKey])

  useEffect(() => {
    const validPassers = new Set(passerOptions.map((item) => item.value))
    setSelectedPasser((previous) => previous && validPassers.has(previous) ? previous : '')
  }, [passerOptionKey])

  useEffect(() => {
    const validReceivers = new Set(receiverOptions.map((item) => item.value))
    setSelectedReceivers((previous) => previous.filter((item) => validReceivers.has(item)))
  }, [receiverOptionKey])

  const selectedReceiverSet = new Set(selectedReceivers)
  const focusConnections = selectedPasser
    ? visibleConnections.filter((connection) => {
        if (s(connection.passer).trim() !== selectedPasser) return false
        if (!selectedReceiverSet.size) return true
        return selectedReceiverSet.has(s(connection.receiver).trim())
      })
    : []
  const visiblePassCount = visibleConnections.reduce((total, connection) => total + n(connection.count), 0)
  const focusPassCount = focusConnections.reduce((total, connection) => total + n(connection.count), 0)
  const focusReceiverCount = new Set(focusConnections.map((connection) => s(connection.receiver).trim()).filter(Boolean)).size
  const displayConnections = selectedPasser ? focusConnections : visibleConnections
  const displayPassCount = selectedPasser ? focusPassCount : visiblePassCount
  const displayPlayerNames = new Set<string>()
  displayConnections.forEach((connection) => {
    const passer = s(connection.passer).trim()
    const receiver = s(connection.receiver).trim()
    if (passer) displayPlayerNames.add(passer)
    if (receiver) displayPlayerNames.add(receiver)
  })
  const displayPlayerCount = selectedPasser ? displayPlayerNames.size : visiblePlayers.length
  const strongest = visibleConnections[0]
  const hiddenSubstituteCount = !showSubstitutes ? substitutePlayerKeySet.size : 0
  const substituteSummary = hiddenSubstituteCount
    ? `${hiddenSubstituteCount} substitute${hiddenSubstituteCount === 1 ? '' : 's'} hidden by default.`
    : ''

  const baseSummary = selectedPasser
    ? `${selectedPasser} to ${selectedReceivers.length ? `${selectedReceivers.length} selected receiver${selectedReceivers.length === 1 ? '' : 's'}` : `${focusReceiverCount} receiver${focusReceiverCount === 1 ? '' : 's'}`} with ${focusPassCount} completed passes.`
    : manualHiddenPlayerKeys.length
      ? `${manualHiddenPlayerKeys.length} player${manualHiddenPlayerKeys.length === 1 ? '' : 's'} manually deselected. Showing ${visiblePlayers.length} players and ${visiblePassCount} completed passes.`
      : strongest
        ? `Strongest link is ${s(strongest.label)} with ${n(strongest.count)} completed passes.`
        : 'No completed open play connections available.'
  const summary = substituteSummary ? `${substituteSummary} ${baseSummary}` : baseSummary

  return (
    <div style={panelStyle({ padding: 16, minWidth: 0 })}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(240px, 330px)', gap: 12, alignItems: 'start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Open play completed pass network. By default, substitutes are hidden so the first view shows the starter network. Select a passer and one or more receivers to isolate link locations, or deselect players to remove them and their links from the network.</div>
        </div>
        <div style={{ display: 'grid', gap: 8, minWidth: 0 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--muted)', minWidth: 0 }}>
            <input
              type="checkbox"
              checked={showSubstitutes}
              onChange={(event) => setShowSubstitutes(event.currentTarget.checked)}
            />
            Show substitutes
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', minWidth: 0 }}>
            Deselect players
            <select
              multiple
              size={Math.min(6, Math.max(3, playerOptions.length || 3))}
              value={manualHiddenPlayerKeys}
              onChange={(event) => setManualHiddenPlayerKeys(Array.from(event.currentTarget.selectedOptions).map((option) => option.value))}
              style={{ ...FIELD_STYLE, marginTop: 6, height: 118 }}
            >
              {playerOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} · {option.count}
                </option>
              ))}
            </select>
            <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Selected names are hidden from the pitch and all their passing links are removed.</div>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', minWidth: 0 }}>
            Passer
            <select
              value={selectedPasser}
              onChange={(event) => {
                setSelectedPasser(event.currentTarget.value)
                setSelectedReceivers([])
              }}
              style={{ ...FIELD_STYLE, marginTop: 6 }}
            >
              <option value="">Whole team network</option>
              {passerOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} · {option.count}
                </option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, color: selectedPasser ? 'var(--muted)' : 'rgba(148,163,184,0.58)', minWidth: 0 }}>
            Receiver or receivers
            <select
              multiple
              disabled={!selectedPasser}
              size={Math.min(6, Math.max(3, receiverOptions.length || 3))}
              value={selectedReceivers}
              onChange={(event) => setSelectedReceivers(Array.from(event.currentTarget.selectedOptions).map((option) => option.value))}
              style={{ ...FIELD_STYLE, marginTop: 6, height: 118, opacity: selectedPasser ? 1 : 0.58 }}
            >
              {receiverOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} · {option.count}
                </option>
              ))}
            </select>
            <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Leave receivers empty to show all links from the selected passer.</div>
          </label>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 8, marginBottom: 10 }}>
        <MetricCard label="Completed passes" value={displayPassCount} note="Open play links with receiver inferred" />
        <MetricCard label="Network players" value={displayPlayerCount} note="Visible players involved in connections" />
        <MetricCard label="Connections" value={displayConnections.length} note="Visible player to player links" />
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 9 }}>
        <span style={{ ...smallInfoStyle(), display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 9, height: 9, borderRadius: 999, background: tone === 'cyan' ? '#2dd8e9' : '#a78bfa', display: 'inline-block' }} /> Starter</span>
        <span style={{ ...smallInfoStyle(), display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 9, height: 9, borderRadius: 999, background: '#f59e0b', display: 'inline-block' }} /> Substitute</span>
      </div>

      <PassNetworkPitch network={data} focusConnections={focusConnections} tone={tone} hiddenPlayerKeys={effectiveHiddenPlayerKeySet} />
      <div style={{ ...smallInfoStyle(), marginTop: 8 }}>{summary}</div>
      {(selectedPasser || manualHiddenPlayerKeys.length > 0) && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 9 }}>
          {selectedPasser && (
            <button type="button" onClick={() => { setSelectedPasser(''); setSelectedReceivers([]) }} style={{ ...buttonStyle(false), padding: '8px 10px' }}>Clear passer filter</button>
          )}
          {manualHiddenPlayerKeys.length > 0 && (
            <button type="button" onClick={() => setManualHiddenPlayerKeys([])} style={{ ...buttonStyle(false), padding: '8px 10px' }}>Clear deselected players</button>
          )}
        </div>
      )}
    </div>
  )
}


type GoalkeeperDistribution = {
  available: boolean
  goalkeeper: string
  source: string
  events: AnyRecord[]
  total: number
  completed: number
  completionRate: number
  shortCount: number
  mediumCount: number
  longCount: number
  launchedCount: number
  progressiveCount: number
  avgLength: number
}

function goalkeeperPositionText(record: AnyRecord | undefined): string {
  if (!record) return ''
  return [
    record.player_position,
    record.position,
    record.position_group,
    record.role,
    record.player_role,
    record.lineup_position,
  ].map((value) => s(value)).join(' ').trim()
}

function looksLikeGoalkeeper(record: AnyRecord | undefined): boolean {
  const text = goalkeeperPositionText(record).toLowerCase()
  if (!text) return false
  return /(^|[^a-z])gk([^a-z]|$)/i.test(text) || text.includes('goalkeeper') || text.includes('goal keeper') || text.includes('keeper')
}

function eventBelongsToSide(event: AnyRecord, side: Side, teamName: string): boolean {
  const eventSide = s(event.team_side).trim().toLowerCase()
  if (eventSide === side) return true

  const eventTeam = s(event.team).trim().toLowerCase()
  return Boolean(eventTeam && teamName.trim() && eventTeam === teamName.trim().toLowerCase())
}

function isDistributionPassEvent(event: AnyRecord): boolean {
  const text = `${s(event.event_kind)} ${s(event.event_type)} ${s(event.type)} ${s(event.type_l)} ${s(event.label)}`.toLowerCase()
  const compact = text.replace(/[^a-z0-9]+/g, '')
  if (compact.includes('substitution') || compact.includes('formation') || compact.includes('card') || compact.includes('offsidegiven') || compact.includes('foul')) return false

  const isPass = b(event.is_pass) || b(event.is_pass_like) || compact.includes('pass') || compact.includes('goalkick')
  const isCross = b(event.is_cross) || compact.includes('cross')
  return isPass && !isCross
}

function distributionLengthBucket(length: number): 'short' | 'medium' | 'long' | 'launched' {
  if (length < 20) return 'short'
  if (length < 40) return 'medium'
  if (length < 60) return 'long'
  return 'launched'
}

function passOutcomeIsSuccessful(event: AnyRecord): boolean {
  return b(event.is_success) || b(event.successful) || b(event.outcome_type) || b(event.outcome_l)
}

function goalkeeperNamesFromSetup(analysis: MatchAnalysisResponse, side: Side): Set<string> {
  const matchSetup = objectFromRecord(analysis as unknown as AnyRecord, 'match_setup')
  const setupSide = objectFromRecord(matchSetup, side)
  const setupPlayers = [
    ...listFromRecord(setupSide, 'starting_xi'),
    ...listFromRecord(setupSide, 'bench'),
    ...listFromRecord(setupSide, 'players'),
  ]
  const names = new Set<string>()

  setupPlayers.forEach((player) => {
    const playerName = s(player.player ?? player.name ?? player.player_name).trim()
    if (playerName && looksLikeGoalkeeper(player)) names.add(passNetworkPlayerKey(playerName))
  })

  return names
}

function inferGoalkeeperNameFromEvents(teamEvents: AnyRecord[]): string {
  const candidates = new Map<string, { player: string; count: number; ownThirdCount: number; xSum: number }>()

  teamEvents.forEach((event) => {
    if (!isDistributionPassEvent(event)) return
    const player = s(event.player ?? event.player_name ?? event.name).trim()
    const x = pctCoord(event.x ?? event.start_x)
    const y = pctCoord(event.y ?? event.start_y)
    const endX = pctCoord(event.end_x)
    const endY = pctCoord(event.end_y)
    if (!player || x === null || y === null || endX === null || endY === null) return

    const key = passNetworkPlayerKey(player)
    const current = candidates.get(key) ?? { player, count: 0, ownThirdCount: 0, xSum: 0 }
    current.count += 1
    current.ownThirdCount += x <= 32 ? 1 : 0
    current.xSum += x
    candidates.set(key, current)
  })

  const ranked = Array.from(candidates.values())
    .filter((candidate) => candidate.count >= 2 && candidate.ownThirdCount >= 1)
    .sort((a, b) => (a.xSum / Math.max(1, a.count)) - (b.xSum / Math.max(1, b.count)) || b.count - a.count)

  return ranked[0]?.player ?? ''
}

function buildGoalkeeperDistribution(analysis: MatchAnalysisResponse, side: Side, teamName: string): GoalkeeperDistribution {
  const rawEvents = recordArray((analysis as unknown as AnyRecord).raw_events)
  const teamEvents = rawEvents.filter((event) => eventBelongsToSide(event, side, teamName))
  const setupGoalkeeperKeys = goalkeeperNamesFromSetup(analysis, side)
  const eventGoalkeeperKeys = new Set<string>()

  teamEvents.forEach((event) => {
    const player = s(event.player ?? event.player_name ?? event.name).trim()
    if (player && looksLikeGoalkeeper(event)) eventGoalkeeperKeys.add(passNetworkPlayerKey(player))
  })

  const goalkeeperKeys = new Set<string>([...setupGoalkeeperKeys, ...eventGoalkeeperKeys])
  let inferredGoalkeeper = ''
  let source = 'Lineup position'

  if (!goalkeeperKeys.size) {
    inferredGoalkeeper = inferGoalkeeperNameFromEvents(teamEvents)
    if (inferredGoalkeeper) {
      goalkeeperKeys.add(passNetworkPlayerKey(inferredGoalkeeper))
      source = 'Inferred from deepest passer'
    }
  }

  const events = teamEvents
    .filter((event) => {
      const player = s(event.player ?? event.player_name ?? event.name).trim()
      return player && goalkeeperKeys.has(passNetworkPlayerKey(player)) && isDistributionPassEvent(event)
    })
    .map((event) => {
      const startX = pctCoord(event.x ?? event.start_x)
      const startY = pctCoord(event.y ?? event.start_y)
      const endX = pctCoord(event.end_x)
      const endY = pctCoord(event.end_y)
      if (startX === null || startY === null || endX === null || endY === null) return null

      const length = Math.hypot(endX - startX, endY - startY)
      const bucket = distributionLengthBucket(length)
      return {
        ...event,
        x: startX,
        y: startY,
        start_x: startX,
        start_y: startY,
        end_x: endX,
        end_y: endY,
        event_type: 'pass',
        event_kind: 'pass',
        successful: passOutcomeIsSuccessful(event),
        is_success: passOutcomeIsSuccessful(event),
        distribution_length: Number(length.toFixed(2)),
        distribution_bucket: bucket,
        progressive: endX - startX >= 25,
      }
    })
    .filter((event): event is AnyRecord => Boolean(event))
    .sort((a, b) => n(a.minute, n(a.expanded_minute)) - n(b.minute, n(b.expanded_minute)))

  const completed = events.filter((event) => b(event.is_success)).length
  const total = events.length
  const lengthTotal = events.reduce((sum, event) => sum + n(event.distribution_length), 0)
  const goalkeeper = events.length ? s(events[0].player, inferredGoalkeeper || 'Goalkeeper') : inferredGoalkeeper || 'Goalkeeper'

  return {
    available: total > 0,
    goalkeeper,
    source,
    events,
    total,
    completed,
    completionRate: total ? (completed / total) * 100 : 0,
    shortCount: events.filter((event) => s(event.distribution_bucket) === 'short').length,
    mediumCount: events.filter((event) => s(event.distribution_bucket) === 'medium').length,
    longCount: events.filter((event) => s(event.distribution_bucket) === 'long').length,
    launchedCount: events.filter((event) => s(event.distribution_bucket) === 'launched').length,
    progressiveCount: events.filter((event) => b(event.progressive)).length,
    avgLength: total ? lengthTotal / total : 0,
  }
}

function goalkeeperDistributionColour(event: AnyRecord, tone: 'cyan' | 'violet'): string {
  if (!b(event.is_success)) return 'rgba(148,163,184,0.50)'
  const bucket = s(event.distribution_bucket)
  if (bucket === 'launched') return '#f59e0b'
  if (bucket === 'long') return '#22c55e'
  return tone === 'cyan' ? '#2dd8e9' : '#a78bfa'
}

function GoalkeeperDistributionLegend({ tone }: { tone: 'cyan' | 'violet' }) {
  const primary = tone === 'cyan' ? '#2dd8e9' : '#a78bfa'
  const items = [
    { label: 'Short or medium', colour: primary, dash: '' },
    { label: 'Long', colour: '#22c55e', dash: '' },
    { label: 'Launched', colour: '#f59e0b', dash: '' },
    { label: 'Incomplete', colour: 'rgba(148,163,184,0.65)', dash: '3 2' },
  ]

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end', marginTop: 7 }}>
      {items.map((item) => (
        <span key={item.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--muted)' }}>
          <svg viewBox="0 0 28 10" style={{ width: 26, height: 10 }}>
            <line x1="2" y1="5" x2="26" y2="5" stroke={item.colour} strokeWidth="2.3" strokeLinecap="round" strokeDasharray={item.dash} />
          </svg>
          {item.label}
        </span>
      ))}
    </div>
  )
}

function GoalkeeperDistributionLayer({ events, tone }: { events: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const pitchTooltip = useSvgPitchTooltip()

  return (
    <g>
      {events.slice(0, 160).map((event, index) => {
        const startX = pctCoord(event.x ?? event.start_x)
        const startY = pctCoord(event.y ?? event.start_y)
        const endX = pctCoord(event.end_x)
        const endY = pctCoord(event.end_y)
        if (startX === null || startY === null || endX === null || endY === null) return null

        const sx = pitchX(startX)
        const sy = pitchY(startY)
        const ex = pitchX(endX)
        const ey = pitchY(endY)
        const colour = goalkeeperDistributionColour(event, tone)
        const success = b(event.is_success)
        const length = n(event.distribution_length)
        const width = s(event.distribution_bucket) === 'launched' ? 1.05 : s(event.distribution_bucket) === 'long' ? 0.92 : 0.76
        const opacity = success ? 0.76 : 0.42
        const dash = success ? '' : '1.5 1.25'
        const angle = Math.atan2(ey - sy, ex - sx)
        const headSize = s(event.distribution_bucket) === 'launched' ? 1.6 : 1.25
        const left = angle + Math.PI * 0.82
        const right = angle - Math.PI * 0.82
        const head = `M${ex},${ey} L${ex + Math.cos(left) * headSize},${ey + Math.sin(left) * headSize} L${ex + Math.cos(right) * headSize},${ey + Math.sin(right) * headSize} Z`
        const tooltip = `${minuteText(event.minute ?? event.expanded_minute)} ${s(event.player, 'Goalkeeper')} distribution. ${s(event.distribution_bucket)} ${Math.round(length)} pitch metres. ${success ? 'Completed' : 'Incomplete'}`

        return (
          <g key={`${s(event.event_index, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
            <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={colour} strokeWidth={width} strokeLinecap="round" strokeDasharray={dash} opacity={opacity} />
            <path d={head} fill={colour} opacity={opacity} />
            <circle cx={sx} cy={sy} r="0.82" fill="rgba(15,23,42,0.88)" stroke={colour} strokeWidth="0.36" opacity="0.92" />
            <circle cx={ex} cy={ey} r={success ? 0.78 : 0.58} fill={success ? colour : 'rgba(15,23,42,0.92)'} stroke={colour} strokeWidth="0.34" opacity={success ? 0.78 : 0.62} />
          </g>
        )
      })}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

function GoalkeeperDistributionPanel({ title, data, tone }: { title: string; data: GoalkeeperDistribution; tone: 'cyan' | 'violet' }) {
  const [bucketFilter, setBucketFilter] = useState('all')
  const [completedOnly, setCompletedOnly] = useState(false)
  const [showHeatFunnel, setShowHeatFunnel] = useState(false)
  const visible = data.events.filter((event) => {
    if (bucketFilter !== 'all' && s(event.distribution_bucket) !== bucketFilter) return false
    if (completedOnly && !b(event.is_success)) return false
    return true
  })
  const heatmap = buildPitchHeatmap(visible, 'end')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={smallInfoStyle()}>{data.available ? `${data.goalkeeper} distribution from open play and restart pass events.` : 'No goalkeeper distribution passes could be identified from the lineup and event data.'}</div>
        </div>
        <div style={{ display: 'grid', justifyItems: 'end', gap: 6, minWidth: 180 }}>
          <div style={{ ...labelStyle(), color: tone === 'cyan' ? '#2dd8e9' : '#a78bfa' }}>{visible.length} passes</div>
          <select value={bucketFilter} onChange={(event) => setBucketFilter(event.currentTarget.value)} style={{ ...FIELD_STYLE, marginTop: 0, padding: '8px 10px', fontSize: 11 }}>
            <option value="all">All lengths</option>
            <option value="short">Short</option>
            <option value="medium">Medium</option>
            <option value="long">Long</option>
            <option value="launched">Launched</option>
          </select>
          <label style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 11, color: 'var(--muted)', cursor: 'pointer' }}>
            <input type="checkbox" checked={completedOnly} onChange={(event) => setCompletedOnly(event.target.checked)} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
            Completed only
          </label>
          <HeatFunnelToggle checked={showHeatFunnel} onChange={setShowHeatFunnel} />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 8, marginBottom: 10 }}>
        <MetricCard label="Passes" value={data.total} note={`${data.completed} completed`} />
        <MetricCard label="Completion" value={`${data.completionRate.toFixed(0)}%`} note="GK pass accuracy" />
        <MetricCard label="Average length" value={data.avgLength.toFixed(1)} note="Pitch metres" />
        <MetricCard label="Progressive" value={data.progressiveCount} note="Advanced 25m or more" />
      </div>

      <PitchCanvas height={330}>
        {visible.length ? (
          <>
            {showHeatFunnel && <PitchHeatLayer heatmap={heatmap} tone={tone} />}
            <GoalkeeperDistributionLayer events={visible} tone={tone} />
          </>
        ) : <EmptyPitchNote label="No goalkeeper distribution for this filter." />}
      </PitchCanvas>
      <GoalkeeperDistributionLegend tone={tone} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
        <span style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>Short <strong style={{ color: 'var(--text)' }}>{data.shortCount}</strong></span>
        <span style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>Medium <strong style={{ color: 'var(--text)' }}>{data.mediumCount}</strong></span>
        <span style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>Long <strong style={{ color: 'var(--text)' }}>{data.longCount}</strong></span>
        <span style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>Launched <strong style={{ color: 'var(--text)' }}>{data.launchedCount}</strong></span>
        <span style={{ border: '1px solid rgba(255,255,255,0.09)', borderRadius: 999, padding: '6px 9px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>{data.source}</span>
      </div>
    </div>
  )
}

function AttackingTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const home = analysis.team_summaries?.home as AnyRecord | undefined
  const away = analysis.team_summaries?.away as AnyRecord | undefined
  const homeName = s(home?.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayName = s(away?.team, analysis.selected_fixture?.away_team ?? 'Away')
  const direction = analysis.attacking_direction ?? { home: [], away: [] }
  const threatDirection = objectFromRecord((analysis as unknown as AnyRecord), 'attacking_threat_lanes')
  const threatBoxes = objectFromRecord((analysis as unknown as AnyRecord), 'attacking_threat_boxes')
  const finalThirdPassMaps = (((analysis as unknown as AnyRecord).final_third_pass_maps ?? {}) as AnyRecord)
  const passNetworks = (((analysis as unknown as AnyRecord).pass_networks ?? {}) as AnyRecord)
  const [useThreatLanes, setUseThreatLanes] = useState(false)
  const [showThreatBoxes, setShowThreatBoxes] = useState(true)
  const homeVolumeLanes = Array.isArray(direction.home) ? direction.home as AnyRecord[] : []
  const awayVolumeLanes = Array.isArray(direction.away) ? direction.away as AnyRecord[] : []
  const homeThreatLanes = listFromRecord(threatDirection, 'home')
  const awayThreatLanes = listFromRecord(threatDirection, 'away')
  const homeLanes = useThreatLanes && homeThreatLanes.length ? homeThreatLanes : homeVolumeLanes
  const awayLanes = useThreatLanes && awayThreatLanes.length ? awayThreatLanes : awayVolumeLanes
  const homeThreatBoxes = objectFromRecord(threatBoxes, 'home')
  const awayThreatBoxes = objectFromRecord(threatBoxes, 'away')
  const homeGoalkeeperDistribution = buildGoalkeeperDistribution(analysis, 'home', homeName)
  const awayGoalkeeperDistribution = buildGoalkeeperDistribution(analysis, 'away', awayName)

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={panelStyle({ padding: 12, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' })}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 950 }}>Attacking lane preference and threat zones</div>
          <div style={smallInfoStyle()}>{useThreatLanes ? 'Lane view is weighted by xG and positive xT.' : 'Lane view uses volume from entries, actions and shots.'}</div>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text)', cursor: 'pointer', userSelect: 'none', fontWeight: 850 }}>
            <input type="checkbox" checked={useThreatLanes} onChange={(event) => setUseThreatLanes(event.target.checked)} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
            Threat weighted lanes
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text)', cursor: 'pointer', userSelect: 'none', fontWeight: 850 }}>
            <input type="checkbox" checked={showThreatBoxes} onChange={(event) => setShowThreatBoxes(event.target.checked)} style={{ width: 13, height: 13, accentColor: 'var(--accent)' }} />
            Show 21 box threat map
          </label>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <DirectionPitchMap title={`${homeName} ${useThreatLanes ? 'threat weighted lanes' : 'attacking lane preference'}`} lanes={homeLanes} tone="cyan" />
        <DirectionPitchMap title={`${awayName} ${useThreatLanes ? 'threat weighted lanes' : 'attacking lane preference'}`} lanes={awayLanes} tone="violet" />
      </div>
      {showThreatBoxes && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
          <ThreatBoxPitchMap title={`${homeName} threat weighted zones`} data={homeThreatBoxes} tone="cyan" />
          <ThreatBoxPitchMap title={`${awayName} threat weighted zones`} data={awayThreatBoxes} tone="violet" />
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 16 }}>
        <PassNetworkPanel title={`${homeName} passing network`} data={objectFromRecord(passNetworks, 'home')} tone="cyan" />
        <PassNetworkPanel title={`${awayName} passing network`} data={objectFromRecord(passNetworks, 'away')} tone="violet" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))', gap: 16 }}>
        <GoalkeeperDistributionPanel title={`${homeName} goalkeeper distribution`} data={homeGoalkeeperDistribution} tone="cyan" />
        <GoalkeeperDistributionPanel title={`${awayName} goalkeeper distribution`} data={awayGoalkeeperDistribution} tone="violet" />
      </div>
      <PossessionChainsViz analysis={analysis} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <BallCarryingMap title={`${homeName} take ons and carries`} events={Array.isArray(analysis.action_maps?.home) ? analysis.action_maps.home as AnyRecord[] : []} tone="cyan" />
        <BallCarryingMap title={`${awayName} take ons and carries`} events={Array.isArray(analysis.action_maps?.away) ? analysis.action_maps.away as AnyRecord[] : []} tone="violet" />
      </div>
      <XTPossessionValuePanel analysis={analysis} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <FinalThirdPassMap title={`${homeName} final third passes`} passes={Array.isArray(finalThirdPassMaps.home) ? finalThirdPassMaps.home as AnyRecord[] : []} />
        <FinalThirdPassMap title={`${awayName} final third passes`} passes={Array.isArray(finalThirdPassMaps.away) ? finalThirdPassMaps.away as AnyRecord[] : []} />
      </div>
      <GoalSequenceBuilder sequences={(analysis.shot_sequences ?? []) as AnyRecord[]} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <GoalShotPitchMap title={`${homeName} shot map`} points={analysis.goalmouth_maps.home as AnyRecord[]} tone="cyan" />
        <GoalShotPitchMap title={`${awayName} shot map`} points={analysis.goalmouth_maps.away as AnyRecord[]} tone="violet" />
      </div>
    </div>
  )
}

function valueFromRecord(record: AnyRecord | undefined, key: string): unknown {
  return record ? record[key] : undefined
}

function listFromRecord(record: AnyRecord | undefined, key: string): AnyRecord[] {
  const value = valueFromRecord(record, key)
  return Array.isArray(value) ? value as AnyRecord[] : []
}

function objectFromRecord(record: AnyRecord | undefined, key: string): AnyRecord {
  const value = valueFromRecord(record, key)
  return value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {}
}

function smallInfoStyle(): CSSProperties {
  return { fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }
}

function DefensiveControlFunnel({ data }: { data: AnyRecord }) {
  const funnel = objectFromRecord(data, 'control_funnel')
  const rateRecord = objectFromRecord(funnel, 'rates')
  const steps = listFromRecord(funnel, 'steps')
  const renderedSteps = steps.length ? steps : [
    { label: 'Opponent attacks', count: n(funnel.opponent_attacks), share_pct: 100 },
    { label: 'Reached final third', count: n(funnel.reached_final_third), share_pct: n(rateRecord.final_third_reach_rate) },
    { label: 'Entered box', count: n(funnel.entered_box), share_pct: n(rateRecord.box_entry_rate) },
    { label: 'Shot', count: n(funnel.shots), share_pct: n(rateRecord.shot_conversion_from_box_entry) },
    { label: 'Goal', count: n(funnel.goals), share_pct: n(rateRecord.goal_conversion_from_shot) },
  ]

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h3 style={titleStyle()}>Defensive Control Funnel</h3>
          <div style={smallInfoStyle()}>Tracks how far opponent attacks survived before becoming box entries, shots, or goals.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{s(data.defending_team)} defending</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 10 }}>
        {renderedSteps.map((step, index) => {
          const share = Math.max(2, Math.min(100, n(step.share_pct, index === 0 ? 100 : 0)))
          return (
            <div key={`${s(step.label)}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 15, padding: 12, background: 'rgba(255,255,255,0.035)' }}>
              <div style={labelStyle()}>{s(step.label)}</div>
              <div style={{ fontSize: 25, fontWeight: 950, marginTop: 7 }}>{n(step.count)}</div>
              <div style={{ height: 7, borderRadius: 999, background: 'rgba(255,255,255,0.08)', marginTop: 10, overflow: 'hidden' }}>
                <div style={{ width: `${share}%`, height: '100%', background: 'linear-gradient(90deg, rgba(45,216,233,0.95), rgba(167,139,250,0.75))' }} />
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 6 }}>{share.toFixed(1)} percent of opponent attacks</div>
            </div>
          )
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, marginTop: 14 }}>
        <MetricCard label="Stopped before box" value={n(funnel.stopped_before_box)} note={`${n(rateRecord.box_stop_rate).toFixed(1)} percent box stop rate`} />
        <MetricCard label="Shot from box entry" value={`${n(rateRecord.shot_conversion_from_box_entry).toFixed(1)}%`} note="Entries that survived into a shot chain" />
        <MetricCard label="Goal from shot" value={`${n(rateRecord.goal_conversion_from_shot).toFixed(1)}%`} note="Shot conversion conceded" />
      </div>
    </div>
  )
}

function ArrowPitchMap({ title, note, arrows, tone, showLegend = true }: { title: string; note: string; arrows: AnyRecord[]; tone: 'cyan' | 'violet' | 'amber'; showLegend?: boolean }) {
  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{title}</h3>
          <div style={smallInfoStyle()}>{note}</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{arrows.length} actions</div>
      </div>
      <div style={{ height: 330 }}>
        <PitchCanvas height={330}>
          {arrows.length ? <PitchArrowLayer arrows={arrows} tone={tone} /> : <EmptyPitchNote />}
        </PitchCanvas>
      </div>
      {showLegend && <EventLegend tone={tone} items={['pass', 'cross', 'take_on', 'carry', 'shot']} />}
    </div>
  )
}

function DefensiveProgressionMap({ data }: { data: AnyRecord }) {
  const allProgressions = listFromRecord(data, 'progression_allowed')
  const [finalThirdOnly, setFinalThirdOnly] = useState(true)
  const [boxOnly, setBoxOnly] = useState(true)
  const [centralOnly, setCentralOnly] = useState(true)
  const [shotLinkedOnly, setShotLinkedOnly] = useState(false)

  const visible = allProgressions.filter((item) => {
    const selected =
      (finalThirdOnly && Boolean(item.final_third_entry))
      || (boxOnly && Boolean(item.box_entry))
      || (centralOnly && Boolean(item.central))
    if (!selected) return false
    if (shotLinkedOnly && !Boolean(item.led_to_shot)) return false
    return true
  })

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>Progression Allowed Map</h3>
          <div style={smallInfoStyle()}>Where they were opened up through successful opponent progression.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" style={buttonStyle(finalThirdOnly)} onClick={() => setFinalThirdOnly((value) => !value)}>Final third</button>
          <button type="button" style={buttonStyle(boxOnly)} onClick={() => setBoxOnly((value) => !value)}>Box entries</button>
          <button type="button" style={buttonStyle(centralOnly)} onClick={() => setCentralOnly((value) => !value)}>Central</button>
          <button type="button" style={buttonStyle(shotLinkedOnly)} onClick={() => setShotLinkedOnly((value) => !value)}>Shot linked only</button>
        </div>
      </div>
      <ArrowPitchMap
        title="Where they were opened up"
        note={`${visible.length} shown from ${allProgressions.length} progression events conceded.`}
        arrows={visible}
        tone="cyan"
      />
    </div>
  )
}


function LaneProtectionPanel({ data }: { data: AnyRecord }) {
  const laneProtection = objectFromRecord(data, 'lane_protection')
  const lanes = listFromRecord(laneProtection, 'lanes')
  const totals = objectFromRecord(laneProtection, 'totals')
  const boxArrows = listFromRecord(data, 'box_entry_arrows')
  const pitchLanes = lanes.map((lane) => ({
    ...lane,
    share_pct: n(lane.box_entries_share_pct, n(lane.final_third_entries_share_pct, n(lane.share_pct))),
  }))

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Central protection and wing forcing</h3>
          <div style={smallInfoStyle()}>Pitch view of where box access was conceded. Strong defending forces access wide and prevents those entries from becoming shots.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{n(totals.box_entries)} box entries conceded</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px, 1.35fr) minmax(280px, 0.65fr)', gap: 16 }}>
        <div>
          <PitchCanvas height={330}>
            {pitchLanes.length || boxArrows.length ? (
              <>
                <PitchLaneLayer lanes={pitchLanes} tone="amber" />
                <PitchArrowLayer arrows={boxArrows} tone="cyan" maxArrows={95} linkedKey="led_to_shot" />
              </>
            ) : <EmptyPitchNote label="No lane protection data available." />}
          </PitchCanvas>
          <div style={{ marginTop: 8 }}>
            <EventLegend tone="amber" items={['heat_funnel', 'carry_path', 'pass', 'shot']} />
          </div>
        </div>
        <div style={{ display: 'grid', gap: 8, alignContent: 'start' }}>
          {lanes.map((lane) => (
            <div key={s(lane.lane)} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 13, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 950 }}>{s(lane.label)}</div>
                <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{n(lane.box_entries_share_pct).toFixed(1)}%</div>
              </div>
              <div style={{ ...smallInfoStyle(), marginTop: 4 }}>
                {n(lane.final_third_entries)} final third entries • {n(lane.box_entries)} box entries • {n(lane.shots)} shots
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function DangerConcededPanel({ data }: { data: AnyRecord }) {
  const heatmaps = objectFromRecord(data, 'danger_heatmaps')
  const arrowsByPhase = objectFromRecord(data, 'box_entry_arrows_by_phase')
  const [mode, setMode] = useState<'all' | 'open_play' | 'set_piece'>('all')
  const modeHeatmaps = objectFromRecord(heatmaps, mode)
  const selectedHeatmaps = Object.keys(modeHeatmaps).length ? modeHeatmaps : heatmaps
  const selectedArrows = listFromRecord(arrowsByPhase, mode).length ? listFromRecord(arrowsByPhase, mode) : listFromRecord(data, 'box_entry_arrows')
  const modeLabel = mode === 'all' ? 'All box entries' : mode === 'open_play' ? 'Open play only' : 'Set pieces only'

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h3 style={titleStyle()}>Danger Conceded: One Direction View</h3>
          <div style={smallInfoStyle()}>Shot danger and box entries are visually overlapped into one attacking direction towards the right hand goal. Raw event rows stay unchanged.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setMode('all')} style={buttonStyle(mode === 'all')}>All box entries</button>
          <button type="button" onClick={() => setMode('open_play')} style={buttonStyle(mode === 'open_play')}>Open play only</button>
          <button type="button" onClick={() => setMode('set_piece')} style={buttonStyle(mode === 'set_piece')}>Set pieces only</button>
        </div>
      </div>
      <div style={{ ...smallInfoStyle(), marginBottom: 12 }}>{modeLabel}. Raw event rows remain unchanged, only this pitch view is normalised.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
        <HeatGridPitch title="Shots conceded" heatmap={objectFromRecord(selectedHeatmaps, 'shots_conceded')} note="Opponent shot locations normalised towards the right hand goal for this view." />
        <HeatGridPitch title="Chance origins conceded" heatmap={objectFromRecord(selectedHeatmaps, 'chance_origins_conceded')} note="Origin or final meaningful action before the shot, kept in the same right attacking direction." />
        <ArrowPitchMap title="Box entry arrows conceded" note={`${selectedArrows.length} shown. Amber means the entry survived into a shot quickly.`} arrows={selectedArrows} tone="amber" />
      </div>
    </div>
  )
}

function DefensiveHeightPanel({ data, embedded = false }: { data: AnyRecord; embedded?: boolean }) {
  const height = objectFromRecord(data, 'defensive_height')
  const points = listFromRecord(height, 'points')
  const zones = listFromRecord(height, 'zones')
  const averageHeight = n(height.average_height)
  const wrapperStyle: CSSProperties = embedded ? { display: 'grid', gap: 14, minWidth: 0 } : panelStyle({ padding: 16 })

  return (
    <div style={wrapperStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
        <div>
          <h3 style={titleStyle()}>{embedded ? 'Team view' : 'Defensive height and block profile'}</h3>
          <div style={smallInfoStyle()}>Uses defensive action locations to describe the height of the block. It is event based, not tracking based team shape.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{s(height.block_label, 'Block signal')}</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1.1fr) minmax(220px, 0.9fr)', gap: 14 }}>
        <div style={{ height: 310 }}>
          <PitchCanvas height={310}>
            <rect x={pitchX(0)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(33.33) - pitchX(0)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(59,130,246,0.06)" />
            <rect x={pitchX(33.33)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(66.67) - pitchX(33.33)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(245,158,11,0.06)" />
            <rect x={pitchX(66.67)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(100) - pitchX(66.67)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(34,197,94,0.06)" />
            <line x1={pitchX(averageHeight)} x2={pitchX(averageHeight)} y1={pitchY(0)} y2={pitchY(100)} stroke="rgba(255,255,255,0.74)" strokeWidth="0.85" strokeDasharray="2 1.2" />
            <text x={pitchX(averageHeight) + 1.5} y="6" fill="rgba(232,234,240,0.82)" fontSize="3.4" fontWeight="900">avg {averageHeight.toFixed(1)}</text>
            {points.length ? <PitchPointLayer points={points} tone="green" maxPoints={220} /> : <EmptyPitchNote label="No located defensive actions." />}
          </PitchCanvas>
        </div>
        <div style={{ display: 'grid', gap: 9, alignContent: 'start' }}>
          {zones.map((zone) => (
            <div key={s(zone.key)} style={{ padding: '10px 11px', borderRadius: 12, background: 'rgba(255,255,255,0.045)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, fontWeight: 900 }}>
                <span>{s(zone.label)}</span>
                <strong>{n(zone.count)}</strong>
              </div>
              <div style={{ height: 7, borderRadius: 999, overflow: 'hidden', background: 'rgba(255,255,255,0.08)', marginTop: 8 }}>
                <div style={{ width: `${Math.max(3, n(zone.share_pct))}%`, height: '100%', background: 'rgba(34,197,94,0.75)' }} />
              </div>
              <div style={{ ...smallInfoStyle(), marginTop: 5 }}>{n(zone.share_pct).toFixed(1)} percent of defensive actions</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}


function defensiveBlockPlayerOption(player: AnyRecord): PassNetworkSelectOption {
  const label = s(player.player).trim()
  return {
    value: passNetworkPlayerKey(label),
    label,
    count: n(player.defensive_actions),
  }
}

function defensiveBlockEventColour(event: AnyRecord): string {
  const kind = s(event.event_kind, s(event.category)).toLowerCase()
  if (kind.includes('interception')) return 'rgba(45,216,233,0.74)'
  if (kind.includes('recovery')) return 'rgba(34,197,94,0.70)'
  if (kind.includes('block')) return 'rgba(167,139,250,0.72)'
  if (kind.includes('clearance')) return 'rgba(245,158,11,0.70)'
  if (kind.includes('duel') || kind.includes('tackle') || kind.includes('challenge')) return 'rgba(248,113,113,0.72)'
  return 'rgba(226,232,240,0.58)'
}

function DefensiveBlockMapPanel({ data, embedded = false }: { data: AnyRecord; embedded?: boolean }) {
  const block = objectFromRecord(data, 'defensive_block_map')
  const summary = objectFromRecord(block, 'summary')
  const players = listFromRecord(block, 'players')
  const events = listFromRecord(block, 'events')
  const categoryMix = listFromRecord(block, 'category_mix')
  const [hiddenPlayerKeys, setHiddenPlayerKeys] = useState<string[]>([])
  const hiddenPlayerKeySet = useMemo(() => new Set(hiddenPlayerKeys), [hiddenPlayerKeys])
  const playerOptions = useMemo(
    () => players
      .map(defensiveBlockPlayerOption)
      .filter((option) => option.value && option.label)
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label)),
    [players],
  )
  const playerOptionKey = playerOptions.map((item) => item.value).join('|')

  useEffect(() => {
    const validPlayerKeys = new Set(playerOptions.map((item) => item.value))
    setHiddenPlayerKeys((previous) => previous.filter((item) => validPlayerKeys.has(item)))
  }, [playerOptionKey])

  const visiblePlayers = useMemo(
    () => players.filter((player) => !hiddenPlayerKeySet.has(passNetworkPlayerKey(player.player))),
    [players, hiddenPlayerKeySet],
  )
  const visibleEvents = useMemo(
    () => events.filter((event) => !hiddenPlayerKeySet.has(passNetworkPlayerKey(event.player))),
    [events, hiddenPlayerKeySet],
  )

  const pitchTooltip = useSvgPitchTooltip()
  const averageX = pctCoord(summary.average_x) ?? 50
  const averageY = pctCoord(summary.average_y) ?? 50
  const maxActions = Math.max(1, ...visiblePlayers.map((player) => n(player.defensive_actions)))
  const locatedActions = n(summary.located_defensive_actions)
  const blockLabel = s(summary.block_label, 'Defensive action block')
  const envelopePoints = visiblePlayers
    .map((player) => ({ x: pctCoord(player.avg_x), y: pctCoord(player.avg_y) }))
    .filter((point): point is { x: number; y: number } => point.x !== null && point.y !== null)
  const minX = envelopePoints.length ? Math.max(0, Math.min(...envelopePoints.map((point) => point.x)) - 4) : 0
  const maxX = envelopePoints.length ? Math.min(100, Math.max(...envelopePoints.map((point) => point.x)) + 4) : 100
  const minY = envelopePoints.length ? Math.max(0, Math.min(...envelopePoints.map((point) => point.y)) - 4) : 0
  const maxY = envelopePoints.length ? Math.min(100, Math.max(...envelopePoints.map((point) => point.y)) + 4) : 100
  const hasBlock = visiblePlayers.length > 0 || visibleEvents.length > 0
  const wrapperStyle: CSSProperties = embedded ? { display: 'grid', gap: 14, minWidth: 0 } : panelStyle({ padding: 16, minWidth: 0 })

  if (!Object.keys(block).length) {
    return null
  }

  return (
    <div style={wrapperStyle}>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(240px, 330px)', gap: 12, alignItems: 'start', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>{embedded ? 'Individual view' : 'Defensive block map'}</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Average player locations from defensive actions. This gives an event based picture of the team shape out of possession, with node size based on defensive action volume.</div>
        </div>
        <label style={{ fontSize: 12, color: 'var(--muted)', minWidth: 0 }}>
          Deselect players
          <select
            multiple
            size={Math.min(6, Math.max(3, playerOptions.length || 3))}
            value={hiddenPlayerKeys}
            onChange={(event) => setHiddenPlayerKeys(Array.from(event.currentTarget.selectedOptions).map((option) => option.value))}
            style={{ ...FIELD_STYLE, marginTop: 6, height: 118 }}
          >
            {playerOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label} · {option.count}
              </option>
            ))}
          </select>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Selected names are hidden from the block map and their defensive action dots are removed.</div>
        </label>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(145px, 1fr))', gap: 8, marginBottom: 12 }}>
        <MetricCard label="Located actions" value={locatedActions} note={`${n(summary.total_defensive_actions)} total defensive actions`} />
        <MetricCard label="Avg block height" value={averageX.toFixed(1)} note={blockLabel} />
        <MetricCard label="High actions" value={n(summary.high_actions)} note="Defensive actions in advanced zones" />
        <MetricCard label="Low actions" value={n(summary.low_actions)} note="Defensive actions in deeper zones" />
        <MetricCard label="Players plotted" value={visiblePlayers.length} note={`${hiddenPlayerKeys.length} hidden`} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px, 1.25fr) minmax(250px, 0.75fr)', gap: 14 }}>
        <div>
          <PitchCanvas height={350}>
            {hasBlock ? (
              <g>
                <rect x={pitchX(0)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(33.33) - pitchX(0)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(59,130,246,0.045)" />
                <rect x={pitchX(33.33)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(66.67) - pitchX(33.33)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(245,158,11,0.045)" />
                <rect x={pitchX(66.67)} y={Math.min(pitchY(0), pitchY(100))} width={pitchX(100) - pitchX(66.67)} height={Math.abs(pitchY(100) - pitchY(0))} fill="rgba(34,197,94,0.045)" />
                {envelopePoints.length >= 2 && (
                  <rect
                    x={pitchX(minX)}
                    y={Math.min(pitchY(minY), pitchY(maxY))}
                    width={Math.max(0.1, pitchX(maxX) - pitchX(minX))}
                    height={Math.abs(pitchY(maxY) - pitchY(minY))}
                    fill="rgba(45,216,233,0.055)"
                    stroke="rgba(45,216,233,0.38)"
                    strokeWidth="0.65"
                    strokeDasharray="2 1.4"
                    rx="1.6"
                  />
                )}
                <line x1={pitchX(averageX)} x2={pitchX(averageX)} y1={pitchY(0)} y2={pitchY(100)} stroke="rgba(255,255,255,0.72)" strokeWidth="0.8" strokeDasharray="2 1.2" />
                <line x1={pitchX(0)} x2={pitchX(100)} y1={pitchY(averageY)} y2={pitchY(averageY)} stroke="rgba(255,255,255,0.22)" strokeWidth="0.55" strokeDasharray="1.8 1.3" />
                <text x={pitchX(averageX) + 1.4} y="6" fill="rgba(232,234,240,0.82)" fontSize="3.4" fontWeight="900">avg height {averageX.toFixed(1)}</text>
                <g>
                  {visibleEvents.slice(0, 220).map((event, index) => {
                    const x = pctCoord(event.x)
                    const y = pctCoord(event.y)
                    if (x === null || y === null) return null
                    const tooltip = `${s(event.minute)}' ${s(event.player, 'Unknown')} ${s(event.category, s(event.type, 'defensive action'))}`
                    return (
                      <circle
                        key={`${s(event.event_index, String(index))}-${index}`}
                        cx={pitchX(x)}
                        cy={pitchY(y)}
                        r={b(event.is_high_regain) ? 1.32 : 0.82}
                        fill={defensiveBlockEventColour(event)}
                        stroke={b(event.is_high_regain) ? 'rgba(187,247,208,0.78)' : 'rgba(255,255,255,0.20)'}
                        strokeWidth="0.32"
                        opacity="0.56"
                        {...pitchTooltip.bind(tooltip)}
                      />
                    )
                  })}
                </g>
                <g>
                  {visiblePlayers.map((player, index) => {
                    const x = pctCoord(player.avg_x)
                    const y = pctCoord(player.avg_y)
                    if (x === null || y === null) return null
                    const actionCount = n(player.defensive_actions)
                    const radius = Math.max(2.65, 0.9 + Math.sqrt(Math.max(0, actionCount) / maxActions) * 3.55)
                    const nodeLabel = getPlayerNodeLabel(player)
                    const nameLabel = playerShortName(player.player)
                    const shirtText = cleanVisibleText(player.shirt_no) ? `#${cleanVisibleText(player.shirt_no)} ` : ''
                    const tooltip = `${shirtText}${s(player.player)}. ${actionCount} defensive actions. Avg position ${n(player.avg_x).toFixed(1)}, ${n(player.avg_y).toFixed(1)}. High ${n(player.high_actions)}, middle ${n(player.middle_actions)}, low ${n(player.low_actions)}.`
                    return (
                      <g key={`${s(player.player, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
                        <circle cx={pitchX(x)} cy={pitchY(y)} r={radius + 1.3} fill="rgba(45,216,233,0.20)" stroke="rgba(255,255,255,0.22)" strokeWidth="0.45" />
                        <circle cx={pitchX(x)} cy={pitchY(y)} r={radius} fill="rgba(45,216,233,0.90)" stroke="rgba(255,255,255,0.82)" strokeWidth="0.62" />
                        {nodeLabel && <text x={pitchX(x)} y={pitchY(y) + 0.9} textAnchor="middle" fill="rgba(2,6,23,0.96)" fontSize="2.6" fontWeight="950">{nodeLabel}</text>}
                        <text x={pitchX(x)} y={pitchY(y) + radius + 4.5} textAnchor="middle" fill="rgba(232,234,240,0.94)" fontSize="3.05" fontWeight="950">{nameLabel}</text>
                      </g>
                    )
                  })}
                </g>
                <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
              </g>
            ) : <EmptyPitchNote label="No located defensive block actions for this selection." />}
          </PitchCanvas>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
            <EventLegend tone="green" items={['interception', 'recovery', 'block', 'clearance', 'duel', 'tackle']} />
            <div style={smallInfoStyle()}>{s(block.note, 'Event based defensive block proxy, not tracking data.')}</div>
          </div>
        </div>

        <div style={{ display: 'grid', gap: 9, alignContent: 'start' }}>
          <div style={{ padding: 12, borderRadius: 14, border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 12, fontWeight: 950, marginBottom: 8 }}>Action mix</div>
            {categoryMix.length ? categoryMix.slice(0, 7).map((item) => (
              <div key={s(item.category)} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 12 }}>
                  <span>{s(item.category)}</span>
                  <strong>{n(item.count)}</strong>
                </div>
                <div style={{ height: 6, borderRadius: 999, overflow: 'hidden', background: 'rgba(255,255,255,0.08)', marginTop: 5 }}>
                  <div style={{ width: `${Math.max(3, n(item.share_pct))}%`, height: '100%', background: 'rgba(45,216,233,0.72)' }} />
                </div>
              </div>
            )) : <div style={smallInfoStyle()}>No action mix available.</div>}
          </div>
          <div style={{ padding: 12, borderRadius: 14, border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 12, fontWeight: 950, marginBottom: 8 }}>Most involved defenders</div>
            {visiblePlayers.slice(0, 8).map((player) => (
              <div key={s(player.player)} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: 8, alignItems: 'center', marginBottom: 7 }}>
                <span style={{ fontSize: 12, fontWeight: 850, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s(player.player)}</span>
                <span style={labelStyle()}>{n(player.defensive_actions)}</span>
              </div>
            ))}
          </div>
          {hiddenPlayerKeys.length > 0 && (
            <button type="button" onClick={() => setHiddenPlayerKeys([])} style={{ ...buttonStyle(false), padding: '8px 10px' }}>Show all players</button>
          )}
        </div>
      </div>
    </div>
  )
}


function DefensiveBlockProfilePanel({ data }: { data: AnyRecord }) {
  const height = objectFromRecord(data, 'defensive_height')
  const block = objectFromRecord(data, 'defensive_block_map')
  const summary = objectFromRecord(block, 'summary')
  const hasTeamView = Object.keys(height).length > 0
  const hasIndividualView = Object.keys(block).length > 0
  const [view, setView] = useState<'team' | 'individual'>('team')

  useEffect(() => {
    if (view === 'individual' && !hasIndividualView && hasTeamView) {
      setView('team')
    }
  }, [view, hasIndividualView, hasTeamView])

  if (!hasTeamView && !hasIndividualView) {
    return null
  }

  const tabStyle = (active: boolean, disabled = false): CSSProperties => ({
    ...buttonStyle(active),
    opacity: disabled ? 0.46 : 1,
    cursor: disabled ? 'not-allowed' : 'pointer',
  })

  const blockLabel = s(summary.block_label, s(height.block_label, 'Block signal'))

  return (
    <div style={panelStyle({ padding: 16, minWidth: 0 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 14 }}>
        <div>
          <h3 style={titleStyle()}>Defensive block profile</h3>
          <div style={smallInfoStyle()}>One combined out of possession view. Team view shows the block height and zone profile, while individual view shows player average defensive action positions.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{blockLabel}</div>
          <button type="button" onClick={() => setView('team')} disabled={!hasTeamView} style={tabStyle(view === 'team', !hasTeamView)}>Team view</button>
          <button type="button" onClick={() => setView('individual')} disabled={!hasIndividualView} style={tabStyle(view === 'individual', !hasIndividualView)}>Individual view</button>
        </div>
      </div>

      {view === 'team' ? (
        <DefensiveHeightPanel data={data} embedded />
      ) : (
        <DefensiveBlockMapPanel data={data} embedded />
      )}
    </div>
  )
}



function DefensiveDisruptionPanel({ data }: { data: AnyRecord }) {
  const disruption = objectFromRecord(data, 'disruption')
  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>Defensive Disruption</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5, marginBottom: 14 }}>Event based view of where defending actions happened and whether opponent attacks were stopped before the box.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(165px, 1fr))', gap: 10 }}>
        <MetricCard label="Avg action height" value={n(disruption.average_defensive_action_height).toFixed(1)} note="Average x location of defensive actions" />
        <MetricCard label="High regains" value={n(disruption.high_regains)} note="Regains in advanced zones" />
        <MetricCard label="Middle actions" value={n(disruption.middle_third_actions)} note="Middle third defensive actions" />
        <MetricCard label="Low actions" value={n(disruption.low_block_actions)} note="Deep defensive actions" />
        <MetricCard label="Stopped before box" value={n(disruption.opponent_attacks_stopped_before_box)} note="Opponent chains ended before box access" />
        <MetricCard label="Forced backward" value={n(disruption.forced_backward_actions)} note={`${n(disruption.forced_backward_share_pct).toFixed(1)} percent of progression events`} />
      </div>
    </div>
  )
}


function PressingEffectPanel({ data }: { data: AnyRecord }) {
  const pressing = objectFromRecord(data, 'pressing_effect')
  const outcomes = listFromRecord(pressing, 'pressure_outcomes')
  const regains = listFromRecord(pressing, 'high_regain_events')
  const hasData = Object.keys(pressing).length > 0

  if (!hasData) {
    return null
  }

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
        <div>
          <h3 style={titleStyle()}>Pressing effect</h3>
          <div style={smallInfoStyle()}>Looks at what happened immediately after pressure actions: forced backwards, wide, long, out of play, or escaped pressure.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{n(pressing.forced_action_rate_pct).toFixed(1)}% forced action rate</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(155px, 1fr))', gap: 10, marginBottom: 14 }}>
        <MetricCard label="Press actions" value={n(pressing.total_press_actions)} note="Defensive actions above the pressure line" />
        <MetricCard label="High press" value={n(pressing.high_press_actions)} note="Actions in advanced zones" />
        <MetricCard label="High regains" value={n(pressing.high_regains)} note="Won high after defending action" />
        <MetricCard label="Counterpress regains" value={n(pressing.counterpress_regains)} note="Regains within five seconds after loss" />
        <MetricCard label="Back passes forced" value={n(pressing.forced_back_passes)} note="Opponent moved backwards after pressure" />
        <MetricCard label="Lateral passes forced" value={n(pressing.forced_lateral_passes)} note="Opponent moved sideways after pressure" />
        <MetricCard label="Long clearances" value={n(pressing.forced_long_clearances)} note="Opponent cleared or went long" />
        <MetricCard label="Out of play forced" value={n(pressing.forced_out_of_play)} note="Failed or boundary type outcome" />
        <MetricCard label="Press to shot" value={n(pressing.press_to_shot_chains)} note="Regain chain produced a shot" />
        <MetricCard label="Escapes allowed" value={n(pressing.press_escapes_allowed)} note={`${n(pressing.escape_rate_pct).toFixed(1)} percent escape rate`} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(340px, 1.2fr) minmax(260px, 0.8fr)', gap: 14 }}>
        <div>
          <PitchCanvas height={300}>
            {outcomes.length || regains.length ? (
              <>
                <PitchPointLayer points={outcomes} tone="amber" maxPoints={120} />
                <PitchPointLayer points={regains} tone="green" maxPoints={80} />
              </>
            ) : <EmptyPitchNote label="No pressure outcome events found." />}
          </PitchCanvas>
          <div style={{ marginTop: 8 }}>
            <EventLegend tone="green" items={['recovery', 'interception', 'tackle', 'pass', 'clearance']} />
          </div>
        </div>
        <div style={{ display: 'grid', gap: 8, alignContent: 'start', maxHeight: 300, overflowY: 'auto' }}>
          {outcomes.slice(0, 12).map((item, index) => (
            <div key={`${s(item.event_index)}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: 9, background: 'rgba(255,255,255,0.04)' }}>
              <div style={{ fontSize: 12, fontWeight: 900 }}>{s(item.pressing_outcome, 'Outcome')} • {s(item.player, 'Unknown')}</div>
              <div style={{ ...smallInfoStyle(), marginTop: 3 }}>{s(item.minute)}' • {s(item.type)} • {s(item.outcome_type)}</div>
            </div>
          ))}
          <div style={smallInfoStyle()}>{s(pressing.note, 'These are event based pressure effect indicators, not guaranteed causality.')}</div>
        </div>
      </div>
    </div>
  )
}

function DefensiveSequencesPanel({ data }: { data: AnyRecord }) {
  const sequences = listFromRecord(data, 'danger_sequences')
  const [selectedId, setSelectedId] = useState('')

  useEffect(() => {
    if (!selectedId && sequences.length) {
      setSelectedId(s(sequences[0]?.sequence_id))
    }
  }, [selectedId, sequences])

  const selected = sequences.find((item) => s(item.sequence_id) === selectedId) ?? sequences[0]
  const actions = Array.isArray(selected?.actions) ? selected.actions as AnyRecord[] : []

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Danger Sequences Conceded</h3>
          <div style={smallInfoStyle()}>Shot and goal chains tagged by the defensive problem that allowed danger to survive.</div>
        </div>
        <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)} style={{ ...FIELD_STYLE, width: 330, marginTop: 0 }}>
          {sequences.map((sequence) => (
            <option key={s(sequence.sequence_id)} value={s(sequence.sequence_id)}>
              {s(sequence.defensive_problem_tag, 'Other')} • {n(sequence.minute).toFixed(1)}' • {s(sequence.player)}
            </option>
          ))}
        </select>
      </div>

      {!selected && <div style={smallInfoStyle()}>No danger sequences were found for this defending team.</div>}

      {selected && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 1.2fr) minmax(280px, 0.8fr)', gap: 16 }}>
          <SequencePitch actions={actions} />
          <div style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
            <div style={{ border: '1px solid rgba(245,158,11,0.35)', borderRadius: 14, padding: 12, background: 'rgba(245,158,11,0.08)' }}>
              <div style={labelStyle()}>Problem tag</div>
              <div style={{ fontSize: 18, fontWeight: 950, marginTop: 5 }}>{s(selected.defensive_problem_tag, 'Other')}</div>
              <div style={{ ...smallInfoStyle(), marginTop: 5 }}>{s(selected.outcome_type)} • {s(selected.players_involved)}</div>
            </div>
            {actions.map((action) => (
              <div key={`${s(action.event_index)}-${s(action.order)}`} style={{ padding: '9px 10px', borderRadius: 12, background: 'rgba(255,255,255,0.045)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div style={{ fontSize: 12, fontWeight: 900 }}>{s(action.order)}. {s(action.player) || 'Unknown'} • {s(action.type) || 'Event'}</div>
                <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{s(action.minute)}' • {s(action.outcome_type)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DefensiveEventAudit({ data }: { data: AnyRecord }) {
  const audit = objectFromRecord(data, 'event_audit')
  const categories = listFromRecord(audit, 'categories')
  const players = listFromRecord(audit, 'players')

  return (
    <details style={panelStyle({ padding: 16 })}>
      <summary style={{ cursor: 'pointer', fontWeight: 950 }}>Defensive Event Audit</summary>
      <div style={{ ...smallInfoStyle(), marginTop: 8 }}>{s(audit.note, 'Event volume audit. Do not read this as defensive quality by itself.')}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginTop: 14 }}>
        <div>
          <h4 style={{ margin: '0 0 8px', fontSize: 14 }}>Action types</h4>
          <div style={{ display: 'grid', gap: 7 }}>
            {categories.map((item) => (
              <div key={s(item.category)} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, padding: '8px 9px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
                <span>{s(item.category)}</span>
                <strong>{n(item.count)} • {n(item.share_pct).toFixed(1)}%</strong>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h4 style={{ margin: '0 0 8px', fontSize: 14 }}>Players</h4>
          <div style={{ display: 'grid', gap: 7 }}>
            {players.map((item) => (
              <div key={s(item.player)} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12, padding: '8px 9px', borderRadius: 10, background: 'rgba(255,255,255,0.04)' }}>
                <span>{s(item.player)}</span>
                <strong>{n(item.defensive_actions)} actions • {n(item.high_regains)} high</strong>
              </div>
            ))}
          </div>
        </div>
      </div>
    </details>
  )
}


function DefensiveLeaders({ data }: { data: AnyRecord }) {
  const players = listFromRecord(data, 'top_defensive_players')
  const eventMap = objectFromRecord(data, 'defensive_player_events')
  const [selectedPlayer, setSelectedPlayer] = useState('')
  const [filter, setFilter] = useState<'all' | 'high' | 'box' | 'wide'>('all')

  useEffect(() => {
    if (!selectedPlayer && players.length) {
      setSelectedPlayer(s(players[0].player))
    }
  }, [selectedPlayer, players])

  const selectedEventsRaw = Array.isArray(eventMap[selectedPlayer]) ? eventMap[selectedPlayer] as AnyRecord[] : []
  const selectedEvents = selectedEventsRaw.filter((event) => {
    if (filter === 'high') return b(event.is_high_regain)
    if (filter === 'box') return b(event.is_box_action)
    if (filter === 'wide') return b(event.is_wide_action)
    return true
  })

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Defensive involvement reader</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Select a player to see where his defensive actions happened on the pitch.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[
            ['all', 'All'],
            ['high', 'High regains'],
            ['box', 'Box actions'],
            ['wide', 'Wide actions'],
          ].map(([key, label]) => (
            <button key={key} type="button" onClick={() => setFilter(key as typeof filter)} style={buttonStyle(filter === key)}>{label}</button>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 0.85fr) minmax(360px, 1.15fr)', gap: 14 }}>
        <div style={{ display: 'grid', gap: 9, alignContent: 'start', maxHeight: 360, overflowY: 'auto' }}>
          {players.map((player, index) => {
            const active = s(player.player) === selectedPlayer
            return (
              <button key={`${s(player.player)}-${index}`} type="button" onClick={() => setSelectedPlayer(s(player.player))} style={{ textAlign: 'left', border: active ? '1px solid rgba(45,216,233,0.7)' : '1px solid rgba(255,255,255,0.08)', borderRadius: 15, padding: 12, background: active ? 'rgba(45,216,233,0.13)' : 'rgba(255,255,255,0.035)', color: 'var(--text)', cursor: 'pointer' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 950 }}>{s(player.player)}</div>
                  <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{n(player.score).toFixed(1)}</div>
                </div>
                <div style={{ ...smallInfoStyle(), marginTop: 7 }}>
                  {n(player.interceptions_recoveries)} interceptions or recoveries • {n(player.high_regains)} high regains • {n(player.blocked_shots)} blocks
                </div>
              </button>
            )
          })}
        </div>
        <div>
          <PitchCanvas height={360}>
            {selectedEvents.length ? (
              <PitchPointLayer points={selectedEvents} tone="green" maxPoints={160} showLabels />
            ) : <EmptyPitchNote label="No defensive events for this player and filter." />}
          </PitchCanvas>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
            <EventLegend tone="green" items={['tackle', 'interception', 'recovery', 'block', 'clearance', 'duel']} />
            <div style={smallInfoStyle()}>{selectedEvents.length} plotted actions for {selectedPlayer || 'selected player'}</div>
          </div>
        </div>
      </div>
    </div>
  )
}


function DuelPointLayer({ points }: { points: AnyRecord[] }) {
  return (
    <g>
      {points.slice(0, 240).map((point, index) => {
        const px = pctCoord(point.x)
        const py = pctCoord(point.y)
        if (px === null || py === null) return null
        const x = pitchX(px)
        const y = pitchY(py)
        const won = b(point.won)
        const aerial = b(point.is_aerial)
        const fill = won ? 'rgba(34,197,94,0.90)' : 'rgba(248,113,113,0.88)'
        const stroke = won ? 'rgba(187,247,208,0.90)' : 'rgba(254,202,202,0.88)'
        const radius = aerial ? 1.75 : 1.18
        return (
          <g key={`${s(point.event_index, String(index))}-${index}`}>
            <circle cx={x} cy={y} r={radius} fill={fill} stroke={stroke} strokeWidth="0.38" opacity="0.92" />
            {aerial && <circle cx={x} cy={y} r={radius + 0.82} fill="none" stroke={stroke} strokeWidth="0.32" opacity="0.62" />}
            <title>{`${s(point.minute)}' ${s(point.player, 'Unknown')} ${aerial ? 'aerial' : 'ground'} duel ${won ? 'won' : 'lost'}`}</title>
          </g>
        )
      })}
    </g>
  )
}

function DuelControlPanel({ data }: { data: AnyRecord }) {
  const duelControl = objectFromRecord(data, 'duel_control')
  const summary = objectFromRecord(duelControl, 'summary')
  const players = listFromRecord(duelControl, 'players')
  const events = listFromRecord(duelControl, 'events')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Duel control</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Won and lost ground and aerial contests by team and player.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{n(summary.win_pct).toFixed(1)}% won</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginBottom: 14 }}>
        <MetricCard label="Total duels" value={n(summary.total)} />
        <MetricCard label="Won" value={n(summary.won)} note={`${n(summary.win_pct).toFixed(1)}%`} />
        <MetricCard label="Lost" value={n(summary.lost)} note={`${n(summary.loss_pct).toFixed(1)}%`} />
        <MetricCard label="Aerial won" value={n(summary.aerial_won)} note={`${n(summary.aerial_win_pct).toFixed(1)}% of ${n(summary.aerial_total)}`} />
        <MetricCard label="Ground won" value={n(summary.ground_won)} note={`${n(summary.ground_win_pct).toFixed(1)}% of ${n(summary.ground_total)}`} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px, 1.05fr) minmax(360px, 0.95fr)', gap: 14 }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', textAlign: 'left' }}>
                <th style={{ padding: '7px 8px' }}>Player</th>
                <th style={{ padding: '7px 8px', textAlign: 'right' }}>Duels</th>
                <th style={{ padding: '7px 8px', textAlign: 'right' }}>Won</th>
                <th style={{ padding: '7px 8px', textAlign: 'right' }}>Lost</th>
                <th style={{ padding: '7px 8px', textAlign: 'right' }}>Won %</th>
                <th style={{ padding: '7px 8px', textAlign: 'right' }}>Aerial</th>
              </tr>
            </thead>
            <tbody>
              {players.slice(0, 12).map((player, index) => (
                <tr key={`${s(player.player)}-${index}`} style={{ borderTop: '1px solid rgba(255,255,255,0.07)' }}>
                  <td style={{ padding: '8px', fontWeight: 850 }}>{s(player.player, 'Unknown')}</td>
                  <td style={{ padding: '8px', textAlign: 'right' }}>{n(player.total)}</td>
                  <td style={{ padding: '8px', textAlign: 'right', color: '#86efac', fontWeight: 850 }}>{n(player.won)}</td>
                  <td style={{ padding: '8px', textAlign: 'right', color: '#fca5a5', fontWeight: 850 }}>{n(player.lost)}</td>
                  <td style={{ padding: '8px', textAlign: 'right' }}>{n(player.win_pct).toFixed(1)}%</td>
                  <td style={{ padding: '8px', textAlign: 'right' }}>{n(player.aerial_won)} / {n(player.aerial_total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <PitchCanvas height={330}>
            {events.length ? <DuelPointLayer points={events} /> : <EmptyPitchNote label="No duel events available." />}
          </PitchCanvas>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', marginTop: 7 }}>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}><span style={{ color: '#22c55e', fontWeight: 950 }}>●</span> Won</span>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}><span style={{ color: '#f87171', fontWeight: 950 }}>●</span> Lost</span>
            <span style={{ fontSize: 10, color: 'var(--muted)' }}>larger ring aerial duel</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function DefensiveInterpretation({ data }: { data: AnyRecord }) {
  const lines = listFromRecord(data, 'interpretation')
  const safeLines = lines.length ? lines : []
  return (
    <div style={panelStyle({ padding: 16, background: 'linear-gradient(180deg, rgba(15,23,42,0.97), rgba(12,20,33,0.97))' })}>
      <h3 style={titleStyle()}>Defensive read</h3>
      <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
        {safeLines.map((line, index) => (
          <div key={index} style={{ padding: '10px 11px', borderRadius: 12, border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.035)', fontSize: 12, color: 'var(--text)', lineHeight: 1.5 }}>
            {s(line)}
          </div>
        ))}
      </div>
    </div>
  )
}

function DefensiveTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [side, setSide] = useState<Side>('home')
  const defensive = (analysis.defensive_analysis ?? {}) as Record<Side, AnyRecord>
  const selected = (defensive[side] ?? {}) as AnyRecord
  const hasData = Object.keys(selected).length > 0
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'

  if (!hasData) {
    return (
      <div style={panelStyle({ padding: 16 })}>
        <h3 style={titleStyle()}>Defensive analysis unavailable</h3>
        <div style={smallInfoStyle()}>Rebuild the processed store or reload the fixture so the defensive analysis payload can be generated.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <h3 style={{ ...titleStyle(), fontSize: 18 }}>Defensive tab</h3>
          <div style={smallInfoStyle()}>Prevention, progression allowed, central protection, disruption, and danger conceded.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setSide('home')} style={buttonStyle(side === 'home')}>{homeName} defending</button>
          <button type="button" onClick={() => setSide('away')} style={buttonStyle(side === 'away')}>{awayName} defending</button>
        </div>
      </div>

      <DefensiveInterpretation data={selected} />
      <DefensiveControlFunnel data={selected} />
      <DuelControlPanel data={selected} />
      <DefensiveProgressionMap data={selected} />
      <LaneProtectionPanel data={selected} />
      <DangerConcededPanel data={selected} />
      <DefensiveBlockProfilePanel data={selected} />
      <DefensiveDisruptionPanel data={selected} />
      <PressingEffectPanel data={selected} />
      <DefensiveSequencesPanel data={selected} />
      <DefensiveLeaders data={selected} />
      <DefensiveEventAudit data={selected} />
    </div>
  )
}

function SetPieceSummaryPanel({ data }: { data: AnyRecord }) {
  const summary = objectFromRecord(data, 'summary')
  const team = s(data.team, 'Team')
  const opponent = s(data.opponent_team, 'Opponent')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ marginBottom: 14 }}>
        <h3 style={titleStyle()}>{team} Set Piece Match Summary</h3>
        <div style={smallInfoStyle()}>Separates attacking restart volume from the restarts defended against {opponent}.</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(155px, 1fr))', gap: 10 }}>
        <MetricCard label="Attacking set pieces" value={n(summary.attacking_set_pieces)} note="All recorded restarts taken" />
        <MetricCard label="Corners" value={n(summary.corners)} note="Corner restarts taken" />
        <MetricCard label="Wide free kicks" value={n(summary.wide_free_kicks)} note="Delivery type free kicks" />
        <MetricCard label="Final third throws" value={n(summary.final_third_throw_ins)} note="Throws starting high" />
        <MetricCard label="Set piece shots" value={n(summary.set_piece_shots)} note={`${n(summary.set_piece_shot_rate).toFixed(1)}% shot rate`} />
        <MetricCard label="Set piece goals" value={n(summary.set_piece_goals)} note={`${n(summary.set_piece_goal_rate).toFixed(1)}% goal rate`} />
        <MetricCard label="Set pieces faced" value={n(summary.defensive_set_pieces_faced)} note="Opponent restarts defended" />
        <MetricCard label="Shots conceded" value={n(summary.shots_conceded_from_set_pieces)} note={`${n(summary.defensive_shot_concession_rate).toFixed(1)}% concession rate`} />
      </div>
    </div>
  )
}


function RoutineGroupCard({ group }: { group: AnyRecord }) {
  const zones = listFromRecord(group, 'top_target_zones')
  const examples = listFromRecord(group, 'examples')
  const shotRate = n(group.shot_rate).toFixed(1)
  const contactRate = n(group.first_contact_win_rate).toFixed(1)

  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 16, padding: 13, background: 'rgba(255,255,255,0.035)', display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 950 }}>{s(group.routine_label, 'Set piece routine')}</div>
          <div style={{ ...smallInfoStyle(), marginTop: 4 }}>{s(group.routine_family)} · {s(group.delivery_pattern)} · {s(group.target_pattern)}</div>
        </div>
        <span style={inlineChipStyle(n(group.goals) > 0 ? 'cyan' : n(group.shots) > 0 ? 'amber' : 'slate')}>{n(group.count)} used</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 7 }}>
        <MetricMini label="Shots" value={n(group.shots)} />
        <MetricMini label="Goals" value={n(group.goals)} />
        <MetricMini label="1st contact" value={n(group.first_contact_won)} />
        <MetricMini label="2nd ball" value={n(group.second_ball_retained)} />
      </div>
      <div style={smallInfoStyle()}>Shot rate {shotRate}% · first contact {contactRate}% · average delivery {n(group.average_delivery_distance).toFixed(1)}m.</div>
      {zones.length > 0 && <div style={smallInfoStyle()}>Main target zones: {zones.map((zone) => `${s(zone.zone)} (${n(zone.count)})`).join(', ')}.</div>}
      {s(group.swing_note) && <div style={{ ...smallInfoStyle(), color: 'rgba(251,191,36,0.95)' }}>{s(group.swing_note)}</div>}
      {examples.length > 0 && (
        <details>
          <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 900, color: 'var(--muted)' }}>Examples</summary>
          <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
            {examples.map((item, index) => (
              <div key={`${s(item.sequence_id)}-${index}`} style={{ fontSize: 11, color: 'rgba(232,234,240,0.86)', padding: 7, borderRadius: 9, background: 'rgba(255,255,255,0.035)' }}>
                {s(item.minute)}' · {s(item.taker, 'Unknown')} · {s(item.target_zone, 'Unknown zone')} {b(item.led_to_goal) ? '· goal' : b(item.led_to_shot) ? '· shot' : ''}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function SetPieceRoutineGroupsPanel({ data }: { data: AnyRecord }) {
  const [mode, setMode] = useState<'attacking' | 'defensive'>('attacking')
  const attackingGroups = listFromRecord(data, 'routine_groups')
  const defensiveGroups = listFromRecord(data, 'defensive_routine_groups')
  const rows = mode === 'attacking' ? attackingGroups : defensiveGroups

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>Set Piece Routine Groups</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Groups restarts by delivery pattern and target area, so you can separate repeatable routines from one off deliveries.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setMode('attacking')} style={buttonStyle(mode === 'attacking')}>Created routines</button>
          <button type="button" onClick={() => setMode('defensive')} style={buttonStyle(mode === 'defensive')}>Routines faced</button>
        </div>
      </div>
      {rows.length === 0 ? (
        <div style={smallInfoStyle()}>No routine groups available for this side of the set piece analysis.</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(270px, 1fr))', gap: 10 }}>
          {rows.slice(0, 8).map((group, index) => <RoutineGroupCard key={`${s(group.routine_key)}-${index}`} group={group} />)}
        </div>
      )}
    </div>
  )
}

function SetPieceInterpretation({ data }: { data: AnyRecord }) {
  const lines = listFromRecord(data, 'interpretation')
  if (!lines.length) return null

  return (
    <div style={panelStyle({ padding: 16, borderColor: 'rgba(245,158,11,0.28)' })}>
      <h3 style={titleStyle()}>Set Piece Interpretation</h3>
      <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
        {lines.map((line, index) => (
          <div key={`${s(line)}-${index}`} style={{ fontSize: 12, color: 'rgba(232,234,240,0.9)', lineHeight: 1.55, padding: 10, borderRadius: 12, background: 'rgba(245,158,11,0.07)', border: '1px solid rgba(245,158,11,0.16)' }}>
            {s(line)}
          </div>
        ))}
      </div>
    </div>
  )
}

function SetPieceHalfPitchZoom({ deliveries }: { deliveries: AnyRecord[] }) {
  const pitchTooltip = useSvgPitchTooltip()
  const visible = deliveries.filter((item) => {
    const startX = pctCoord(item.x ?? item.start_x)
    const endX = pctCoord(item.end_x ?? item.x)
    const startY = pctCoord(item.y ?? item.start_y)
    const endY = pctCoord(item.end_y ?? item.y)
    return startX !== null && endX !== null && startY !== null && endY !== null
  })

  const xFor = (value: unknown) => {
    const x = pctCoord(value)
    const clamped = x === null ? 50 : Math.max(50, Math.min(100, x))
    return ((clamped - 50) / 50) * 100
  }
  const yFor = (value: unknown) => {
    const y = pctCoord(value)
    return 100 - (y === null ? 50 : y)
  }
  const arrowHead = (sx: number, sy: number, ex: number, ey: number) => {
    const angle = Math.atan2(ey - sy, ex - sx)
    const size = 2.1
    const left = angle + Math.PI * 0.82
    const right = angle - Math.PI * 0.82
    return `M${ex},${ey} L${ex + Math.cos(left) * size},${ey + Math.sin(left) * size} L${ex + Math.cos(right) * size},${ey + Math.sin(right) * size} Z`
  }

  return (
    <div style={panelStyle({ padding: 14, boxShadow: 'none', minWidth: 0 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div>
          <h4 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>Attacking half delivery zoom</h4>
          <div style={smallInfoStyle()}>Zoomed from the half way line to goal so delivery aim and landing zones are easier to read.</div>
        </div>
        <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{visible.length} deliveries</div>
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ width: '100%', height: 360, display: 'block', borderRadius: 16, background: 'linear-gradient(180deg, rgba(15,23,42,0.96), rgba(2,6,23,0.98))', border: '1px solid rgba(255,255,255,0.08)' }}>
        <rect x="1" y="3" width="98" height="94" rx="2" fill="rgba(255,255,255,0.018)" stroke="rgba(255,255,255,0.20)" strokeWidth="0.45" />
        <line x1="1" y1="3" x2="1" y2="97" stroke="rgba(255,255,255,0.16)" strokeWidth="0.45" />
        <line x1="99" y1="3" x2="99" y2="97" stroke="rgba(255,255,255,0.24)" strokeWidth="0.55" />
        <rect x="66" y="21.1" width="33" height="57.8" fill="rgba(45,216,233,0.045)" stroke="rgba(255,255,255,0.18)" strokeWidth="0.4" />
        <rect x="88" y="36" width="11" height="28" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.18)" strokeWidth="0.35" />
        <circle cx="78" cy="50" r="1" fill="rgba(255,255,255,0.45)" />
        <path d="M66,35 C72,40 72,60 66,65" fill="none" stroke="rgba(255,255,255,0.15)" strokeWidth="0.35" />
        {visible.map((item, index) => {
          const sx = xFor(item.x ?? item.start_x)
          const sy = yFor(item.y ?? item.start_y)
          const ex = xFor(item.end_x ?? item.x)
          const ey = yFor(item.end_y ?? item.y)
          const linked = Boolean(item.led_to_shot)
          const colour = linked ? 'rgba(245,158,11,0.96)' : 'rgba(45,216,233,0.78)'
          const strokeWidth = linked ? 0.85 : 0.58
          const tooltip = `${s(item.minute)}' ${s(item.player)} ${s(item.routine_label, s(item.set_piece_type, s(item.type, 'delivery')))} to ${n(item.end_x ?? item.x).toFixed(1)}, ${n(item.end_y ?? item.y).toFixed(1)}${linked ? ' shot linked' : ''}`
          return (
            <g key={`${index}-${sx}-${sy}-${ex}-${ey}`} {...pitchTooltip.bind(tooltip)}>
              <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={colour} strokeWidth={strokeWidth} strokeLinecap="round" opacity={linked ? 0.9 : 0.62} />
              <path d={arrowHead(sx, sy, ex, ey)} fill={colour} opacity={linked ? 0.95 : 0.72} />
              <circle cx={ex} cy={ey} r={linked ? 1.65 : 1.25} fill={colour} opacity={linked ? 0.95 : 0.72} stroke="rgba(255,255,255,0.55)" strokeWidth="0.22" />
              <title>{`${s(item.minute)}' ${s(item.player)} ${s(item.set_piece_type, s(item.type, 'delivery'))} to ${n(item.end_x ?? item.x).toFixed(1)}, ${n(item.end_y ?? item.y).toFixed(1)}${linked ? ' • shot linked' : ''}`}</title>
            </g>
          )
        })}
        {!visible.length && <text x="50" y="51" textAnchor="middle" fill="rgba(232,234,240,0.6)" fontSize="3.6" fontWeight="850">No delivery data to draw.</text>}
        <SvgPitchTooltip tooltip={pitchTooltip.tooltip} viewBoxWidth={100} viewBoxHeight={100} />
      </svg>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: 'var(--muted)', fontSize: 11, marginTop: 8 }}>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(45,216,233,0.78)', marginRight: 6, verticalAlign: -1 }} />Delivery target</span>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 999, background: 'rgba(245,158,11,0.96)', marginRight: 6, verticalAlign: -1 }} />Led to shot</span>
      </div>
    </div>
  )
}

function SetPieceDeliveryMap({ data }: { data: AnyRecord }) {
  const deliveries = listFromRecord(data, 'delivery_map')
  const [showCorners, setShowCorners] = useState(true)
  const [showFreeKicks, setShowFreeKicks] = useState(true)
  const [showThrows, setShowThrows] = useState(true)
  const [shotLinkedOnly, setShotLinkedOnly] = useState(false)
  const [routineFilter, setRoutineFilter] = useState('all')
  const routineOptions = useMemo(() => Array.from(new Set(deliveries.map((item) => s(item.routine_label)).filter(Boolean))).sort(), [deliveries])

  useEffect(() => {
    if (routineFilter !== 'all' && !routineOptions.includes(routineFilter)) {
      setRoutineFilter('all')
    }
  }, [routineFilter, routineOptions])

  const visible = deliveries.filter((item) => {
    const type = s(item.set_piece_type).toLowerCase()
    const typeMatch =
      (showCorners && type.includes('corner'))
      || (showFreeKicks && type.includes('free kick'))
      || (showThrows && type.includes('throw'))
      || type.includes('penalty')
    if (!typeMatch) return false
    if (routineFilter !== 'all' && s(item.routine_label) !== routineFilter) return false
    if (shotLinkedOnly && !Boolean(item.led_to_shot)) return false
    return true
  })

  return (
    <div style={{ display: 'grid', gap: 10, minWidth: 0 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button type="button" onClick={() => setShowCorners((value) => !value)} style={buttonStyle(showCorners)}>Corners</button>
        <button type="button" onClick={() => setShowFreeKicks((value) => !value)} style={buttonStyle(showFreeKicks)}>Free kicks</button>
        <button type="button" onClick={() => setShowThrows((value) => !value)} style={buttonStyle(showThrows)}>Throws</button>
        <button type="button" onClick={() => setShotLinkedOnly((value) => !value)} style={buttonStyle(shotLinkedOnly)}>Shot linked only</button>
        {routineOptions.length > 0 && (
          <select value={routineFilter} onChange={(event) => setRoutineFilter(event.currentTarget.value)} style={{ ...FIELD_STYLE, width: 260, marginTop: 0, padding: '8px 10px' }}>
            <option value="all">All routines</option>
            {routineOptions.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 14, minWidth: 0 }}>
        <ArrowPitchMap title="Attacking Delivery Map" note="Restart deliveries and targets. Amber indicates the sequence became a shot." arrows={visible} tone="amber" />
        <SetPieceHalfPitchZoom deliveries={visible} />
      </div>
    </div>
  )
}

function SetPieceFirstContactPanel({ data }: { data: AnyRecord }) {
  const contacts = objectFromRecord(data, 'first_contact_and_second_ball')
  const attacking = objectFromRecord(contacts, 'attacking')
  const defensive = objectFromRecord(contacts, 'defensive')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>First Contact and Second Ball</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5, marginBottom: 14 }}>Delivery volume alone is weak. This panel checks whether restarts produced first contact control, second ball retention, and shot follow up.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
        <MetricCard label="First contact won" value={n(attacking.first_contact_won)} note={`${n(attacking.first_contact_win_rate).toFixed(1)}% attacking win rate`} />
        <MetricCard label="First contact lost" value={n(attacking.first_contact_lost)} note="Opponent or loose first contact" />
        <MetricCard label="Second balls retained" value={n(attacking.second_ball_retained)} note="Attacking team kept the next action" />
        <MetricCard label="Shot after contact" value={n(attacking.shot_after_first_contact)} note="First contact survived into shot" />
        <MetricCard label="Defensive contact won" value={n(defensive.first_contact_won)} note={`${n(defensive.first_contact_win_rate).toFixed(1)}% defensive win rate`} />
        <MetricCard label="Clearance or block" value={n(defensive.clearances_or_blocks)} note="Event based defensive response" />
      </div>
    </div>
  )
}

function SetPieceThreatPanel({ data }: { data: AnyRecord }) {
  const attackingThreat = objectFromRecord(data, 'attacking_threat')
  const defensiveProtection = objectFromRecord(data, 'defensive_protection')
  const attackingHeatmaps = objectFromRecord(attackingThreat, 'heatmaps')
  const defensiveHeatmaps = objectFromRecord(defensiveProtection, 'heatmaps')

  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>Attacking Threat and Defensive Protection</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5, marginBottom: 14 }}>Delivery targets, shot zones and conceded restart pressure are separated so set piece quality is not judged by volume alone.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
        <HeatGridPitch title="Delivery target zones" heatmap={objectFromRecord(attackingHeatmaps, 'delivery_targets')} note="Where attacking deliveries were aimed." />
        <HeatGridPitch title="Set piece shots" heatmap={objectFromRecord(attackingHeatmaps, 'set_piece_shots')} note="Shots created from attacking restarts." />
        <HeatGridPitch title="Set piece shots conceded" heatmap={objectFromRecord(defensiveHeatmaps, 'set_piece_shots')} note="Opponent restart shots defended." />
      </div>
    </div>
  )
}

function SetPieceSequencesPanel({ data }: { data: AnyRecord }) {
  const attackingThreat = objectFromRecord(data, 'attacking_threat')
  const defensiveProtection = objectFromRecord(data, 'defensive_protection')
  const attackingSequences = listFromRecord(attackingThreat, 'dangerous_sequences')
  const defensiveSequences = listFromRecord(defensiveProtection, 'dangerous_sequences_conceded')
  const allSequences: AnyRecord[] = [
    ...attackingSequences.map((item) => ({ ...item, view_label: 'Created' })),
    ...defensiveSequences.map((item) => ({ ...item, view_label: 'Conceded' })),
  ]

  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>Set Piece Sequences</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5, marginBottom: 12 }}>Only sequences that became shots, goals or clear danger are listed first.</div>
      <div style={{ display: 'grid', gap: 10 }}>
        {allSequences.length === 0 && <div style={smallInfoStyle()}>No set piece sequence survived into a recorded shot or goal.</div>}
        {allSequences.map((sequence, index) => {
          const delivery = objectFromRecord(sequence, 'delivery')
          const firstContact = objectFromRecord(sequence, 'first_contact')
          const shot = objectFromRecord(sequence, 'shot')
          const actions = listFromRecord(sequence, 'actions')
          return (
            <details key={`${s(sequence.sequence_id)}-${index}`} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 12, background: 'rgba(255,255,255,0.035)' }}>
              <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 950 }}>
                {s(sequence.view_label)} | {n(sequence.minute).toFixed(1)}' | {s(sequence.restart_type)} | {s(sequence.tag)} {Boolean(sequence.led_to_goal) ? '| Goal' : Boolean(sequence.led_to_shot) ? '| Shot' : ''}
              </summary>
              <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
                <div style={smallInfoStyle()}>
                  Taker: {s(sequence.taker, 'Unknown')} | Target: {s(delivery.target_zone, 'Unknown')} | First contact: {s(firstContact.player, 'Unknown')} | Shot: {s(shot.player, 'None')}
                </div>
                <div style={{ display: 'grid', gap: 6 }}>
                  {actions.slice(0, 10).map((action, actionIndex) => (
                    <div key={`${s(action.event_index)}-${actionIndex}`} style={{ fontSize: 12, color: 'rgba(232,234,240,0.88)', padding: 8, borderRadius: 10, background: 'rgba(255,255,255,0.035)' }}>
                      {n(action.order)}. {s(action.minute)}' | {s(action.team)} | {s(action.player, 'Unknown')} | {s(action.type, 'Event')} | {s(action.outcome_type)}
                    </div>
                  ))}
                </div>
              </div>
            </details>
          )
        })}
      </div>
    </div>
  )
}

function InvolvementList({ title, rows, valueKey }: { title: string; rows: AnyRecord[]; valueKey: string }) {
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 12, background: 'rgba(255,255,255,0.035)' }}>
      <div style={{ fontSize: 13, fontWeight: 950, marginBottom: 8 }}>{title}</div>
      <div style={{ display: 'grid', gap: 7 }}>
        {rows.length === 0 && <div style={smallInfoStyle()}>No recorded involvement.</div>}
        {rows.map((row, index) => (
          <div key={`${s(row.player)}-${index}`} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 12 }}>
            <span>{s(row.player, 'Unknown')}</span>
            <strong>{n(row[valueKey])}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}

function SetPieceInvolvementPanel({ data }: { data: AnyRecord }) {
  const involvement = objectFromRecord(data, 'involvement')
  return (
    <div style={panelStyle({ padding: 16 })}>
      <h3 style={titleStyle()}>Set Piece Involvement Leaders</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5, marginBottom: 12 }}>This is involvement, not proof of set piece quality.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
        <InvolvementList title="Main takers" rows={listFromRecord(involvement, 'takers')} valueKey="set_pieces_taken" />
        <InvolvementList title="First contact players" rows={listFromRecord(involvement, 'first_contact_players')} valueKey="first_contacts" />
        <InvolvementList title="Shot takers" rows={listFromRecord(involvement, 'shot_takers')} valueKey="set_piece_shots" />
        <InvolvementList title="Defensive clearers" rows={listFromRecord(involvement, 'defensive_clearers')} valueKey="clearances" />
        <InvolvementList title="Blockers" rows={listFromRecord(involvement, 'blockers')} valueKey="blocks" />
      </div>
    </div>
  )
}

function SetPiecesTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [side, setSide] = useState<Side>('home')
  const setPieces = (analysis.set_piece_analysis ?? {}) as Record<Side, AnyRecord>
  const selected = (setPieces[side] ?? {}) as AnyRecord
  const hasData = Object.keys(selected).length > 0
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'

  if (!hasData) {
    return (
      <div style={panelStyle({ padding: 16 })}>
        <h3 style={titleStyle()}>Set piece analysis unavailable</h3>
        <div style={smallInfoStyle()}>Reload the fixture so the set piece analysis payload can be generated.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <h3 style={{ ...titleStyle(), fontSize: 18 }}>Set pieces tab</h3>
          <div style={smallInfoStyle()}>Restart creation, delivery targets, first contact, second balls, and defensive protection.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setSide('home')} style={buttonStyle(side === 'home')}>{homeName} set pieces</button>
          <button type="button" onClick={() => setSide('away')} style={buttonStyle(side === 'away')}>{awayName} set pieces</button>
        </div>
      </div>

      <SetPieceInterpretation data={selected} />
      <SetPieceSummaryPanel data={selected} />
      <SetPieceRoutineGroupsPanel data={selected} />
      <SetPieceDeliveryMap data={selected} />
      <SetPieceFirstContactPanel data={selected} />
      <SetPieceThreatPanel data={selected} />
      <SetPieceSequencesPanel data={selected} />
      <SetPieceInvolvementPanel data={selected} />
    </div>
  )
}


function StandardEventMapPanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const home = analysis.team_summaries?.home as AnyRecord | undefined
  const away = analysis.team_summaries?.away as AnyRecord | undefined
  const homeName = s(home?.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayName = s(away?.team, analysis.selected_fixture?.away_team ?? 'Away')
  const homeEvents = Array.isArray(analysis.action_maps?.home) ? analysis.action_maps.home as AnyRecord[] : []
  const awayEvents = Array.isArray(analysis.action_maps?.away) ? analysis.action_maps.away as AnyRecord[] : []

  return (
    <div style={{ marginTop: 14 }}>
      <h3 style={{ ...titleStyle(), fontSize: 15 }}>Standard event maps</h3>
      <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.45, marginTop: 8 }}>
        Shared on pitch event map.
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16, marginTop: 14 }}>
        <div style={panelStyle({ padding: 14, boxShadow: 'none' })}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
            <h3 style={{ ...titleStyle(), fontSize: 15 }}>{homeName} event map</h3>
            <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{homeEvents.length} events</div>
          </div>
          <PitchCanvas height={330}>
            {homeEvents.length ? <PitchPointLayer points={homeEvents} tone="cyan" maxPoints={280} /> : <EmptyPitchNote />}
          </PitchCanvas>
          <EventLegend tone="cyan" items={['pass', 'cross', 'take_on', 'carry', 'shot', 'goal', 'tackle', 'interception', 'recovery']} />
        </div>
        <div style={panelStyle({ padding: 14, boxShadow: 'none' })}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
            <h3 style={{ ...titleStyle(), fontSize: 15 }}>{awayName} event map</h3>
            <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{awayEvents.length} events</div>
          </div>
          <PitchCanvas height={330}>
            {awayEvents.length ? <PitchPointLayer points={awayEvents} tone="violet" maxPoints={280} /> : <EmptyPitchNote />}
          </PitchCanvas>
          <EventLegend tone="violet" items={['pass', 'cross', 'take_on', 'carry', 'shot', 'goal', 'tackle', 'interception', 'recovery']} />
        </div>
      </div>
    </div>
  )
}

function MatchAuditPanel({ analysis, rawColumns, reportView = false }: { analysis: MatchAnalysisResponse; rawColumns: string[]; reportView?: boolean }) {
  const summaryCards = (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginTop: 14 }}>
      <MetricCard label="Data source" value={s(analysis.data_source, 'raw_csv')} note={analysis.processed_store?.exists ? 'Processed store available' : 'Raw CSV fallback'} />
      <MetricCard label="Raw event rows" value={analysis.event_count} />
      <MetricCard label="Analytic rows" value={n((analysis as AnyRecord).analytic_event_count, analysis.event_count)} note="Includes inferred carry rows used for visuals and team stats" />
      <MetricCard label="Available columns" value={analysis.available_columns.length} />
    </div>
  )

  if (reportView) {
    return (
      <section style={panelStyle({ padding: 18, marginTop: 16 })}>
        <h2 style={titleStyle()}>Data confidence summary</h2>
        <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Raw audit tables are hidden in Report view. Turn Report view off to inspect the event rows and map audit.</div>
        {summaryCards}
      </section>
    )
  }

  return (
    <section style={panelStyle({ padding: 18, marginTop: 16 })}>
      <details>
        <summary style={{ cursor: 'pointer', fontWeight: 950, fontSize: 15 }}>Data audit and confidence</summary>
        {summaryCards}
        <StandardEventMapPanel analysis={analysis} />
        <div style={{ marginTop: 16 }}>
          <h3 style={{ ...titleStyle(), fontSize: 15 }}>Raw event validation table</h3>
          <div style={{ marginTop: 12 }}>
            <DataTable columns={rawColumns} rows={toRows(analysis.raw_events)} maxRows={500} height={420} />
          </div>
        </div>
      </details>
    </section>
  )
}



function BestPlayersTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [side, setSide] = useState<Side>('home')
  const [category, setCategory] = useState<'overall' | 'attacking' | 'defensive' | 'transitions' | 'set_pieces'>('overall')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'
  const best = ((analysis as unknown as AnyRecord).best_players_analysis ?? {}) as Record<Side, AnyRecord>
  const selected = (best[side] ?? {}) as AnyRecord
  const rows = listFromRecord(selected, category)
  const scoreKeyByCategory: Record<typeof category, string> = {
    overall: 'overall_score',
    attacking: 'attacking_score',
    defensive: 'defensive_score',
    transitions: 'transition_score',
    set_pieces: 'set_piece_score',
  }
  const scoreKey = scoreKeyByCategory[category]
  const safeSelectedIndex = rows.length ? Math.min(selectedIndex, rows.length - 1) : 0
  const selectedPlayer = rows[safeSelectedIndex]
  const selectedPlayerName = s(selectedPlayer?.player, '')
  const teamEvents = Array.isArray(analysis.action_maps?.[side]) ? analysis.action_maps?.[side] as AnyRecord[] : []
  const playerEvents = selectedPlayerName
    ? teamEvents.filter((event) => s(event.player).trim().toLowerCase() === selectedPlayerName.trim().toLowerCase())
    : []
  const playerHeatmap = buildPitchHeatmap(playerEvents, 'start')
  const selectedTone = side === 'home' ? 'cyan' : 'violet'

  const switchSide = (nextSide: Side) => {
    setSide(nextSide)
    setSelectedIndex(0)
  }

  const switchCategory = (nextCategory: typeof category) => {
    setCategory(nextCategory)
    setSelectedIndex(0)
  }

  return (
    <div style={{ display: 'grid', gap: 14, minWidth: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <h3 style={{ ...titleStyle(), fontSize: 18 }}>Best players</h3>
          <div style={smallInfoStyle()}>Click a player card to show his match actions and heat map on the pitch.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => switchSide('home')} style={buttonStyle(side === 'home')}>{homeName}</button>
          <button type="button" onClick={() => switchSide('away')} style={buttonStyle(side === 'away')}>{awayName}</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {[
          ['overall', 'Overall'],
          ['attacking', 'Attacking'],
          ['defensive', 'Defensive'],
          ['transitions', 'Transitions'],
          ['set_pieces', 'Set pieces'],
        ].map(([key, label]) => (
          <button key={key} type="button" onClick={() => switchCategory(key as typeof category)} style={buttonStyle(category === key)}>{label}</button>
        ))}
      </div>

      {!rows.length && <div style={panelStyle({ padding: 16 })}><div style={smallInfoStyle()}>Best player data is not available for this fixture yet.</div></div>}

      {!!rows.length && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 0.85fr) minmax(420px, 1.15fr)', gap: 14, alignItems: 'start', minWidth: 0 }}>
          <div style={{ display: 'grid', gap: 10, minWidth: 0 }}>
            {rows.map((player, index) => {
              const reasons = Array.isArray(player.reasons) ? player.reasons as unknown[] : []
              const active = index === safeSelectedIndex
              return (
                <button
                  key={`${s(player.player)}-${index}`}
                  type="button"
                  onClick={() => setSelectedIndex(index)}
                  style={{
                    ...panelStyle({ padding: 14, boxShadow: 'none' }),
                    border: active ? '1px solid rgba(45,216,233,0.58)' : '1px solid rgba(255,255,255,0.08)',
                    background: active ? 'linear-gradient(180deg, rgba(45,216,233,0.12), rgba(15,23,42,0.92))' : 'rgba(255,255,255,0.035)',
                    color: 'var(--text)',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 950, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s(player.player, 'Unknown')}</div>
                      <div style={{ ...smallInfoStyle(), marginTop: 3 }}>{s(player.team)}</div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ ...labelStyle(), color: active ? 'var(--accent)' : 'var(--muted)' }}>{category.replaceAll('_', ' ')}</div>
                      <div style={{ fontSize: 20, fontWeight: 950 }}>{n(player[scoreKey]).toFixed(1)}</div>
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 7, marginTop: 12 }}>
                    <MetricMini label="Shots" value={n(player.shots)} />
                    <MetricMini label="Box entries" value={n(player.box_entries)} />
                    <MetricMini label="High regains" value={n(player.high_regains)} />
                    <MetricMini label="Def actions" value={n(player.defensive_actions)} />
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 11 }}>
                    {reasons.length ? reasons.map((reason, reasonIndex) => (
                      <span key={`${s(reason)}-${reasonIndex}`} style={{ border: '1px solid rgba(255,255,255,0.1)', borderRadius: 999, padding: '5px 8px', fontSize: 11, color: 'var(--muted)', background: 'rgba(255,255,255,0.035)' }}>{s(reason)}</span>
                    )) : <span style={smallInfoStyle()}>No standout tag available.</span>}
                  </div>
                </button>
              )
            })}
          </div>

          <div style={panelStyle({ padding: 14, boxShadow: 'none', minWidth: 0 })}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
              <div>
                <h4 style={{ margin: 0, fontSize: 15, fontWeight: 950 }}>{selectedPlayerName || 'Select player'}</h4>
                <div style={smallInfoStyle()}>Actions shown from the shared match event map for the selected player.</div>
              </div>
              <div style={{ ...labelStyle(), color: 'var(--accent)' }}>{playerEvents.length} mapped actions</div>
            </div>
            <PitchCanvas height={390}>
              {playerEvents.length ? (
                <>
                  <PitchHeatLayer heatmap={playerHeatmap} tone={selectedTone} />
                  <PitchArrowLayer arrows={playerEvents} tone={selectedTone} maxArrows={160} />
                  <PitchPointLayer points={playerEvents} tone={selectedTone} maxPoints={160} />
                </>
              ) : <EmptyPitchNote label="No mapped actions for this player." />}
            </PitchCanvas>
            <div style={{ marginTop: 8 }}>
              <EventLegend tone={selectedTone} items={['pass', 'cross', 'carry_path', 'shot', 'defensive_action', 'heat_funnel']} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function inlineChipStyle(tone: 'cyan' | 'violet' | 'amber' | 'slate' = 'slate'): CSSProperties {
  const colour = tone === 'cyan' ? '#2dd8e9' : tone === 'violet' ? '#a78bfa' : tone === 'amber' ? '#f59e0b' : 'rgba(226,232,240,0.82)'
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    border: `1px solid ${tone === 'slate' ? 'rgba(255,255,255,0.10)' : colour}`,
    background: tone === 'slate' ? 'rgba(255,255,255,0.045)' : `${colour}1f`,
    color: colour,
    borderRadius: 999,
    padding: '3px 7px',
    fontSize: 10.5,
    fontWeight: 850,
    lineHeight: 1.2,
    whiteSpace: 'nowrap',
  }
}

function ConfidenceNotes({ notes }: { notes: unknown }) {
  const rows = Array.isArray(notes) ? notes.map((item) => s(item)).filter(Boolean) : []
  if (!rows.length) return null
  return (
    <div style={{ marginTop: 10, display: 'grid', gap: 3 }}>
      {rows.map((note, index) => <div key={`${note}-${index}`} style={{ ...smallInfoStyle(), fontSize: 11 }}>{note}</div>)}
    </div>
  )
}

function lineupRoleText(player: AnyRecord): string {
  return `${s(player.position)} ${s(player.player_position)} ${s(player.position_group)}`.trim().toUpperCase()
}

function compactLineupRole(player: AnyRecord): string {
  return lineupRoleText(player).replace(/[^A-Z0-9]+/g, '')
}

function lineupLineKey(player: AnyRecord): 'gk' | 'def' | 'mid' | 'am' | 'fw' {
  const role = lineupRoleText(player)
  const compact = compactLineupRole(player)

  if (role.includes('GK')) return 'gk'
  if (/(^|[^A-Z])(CF|ST|FWC|FW\(C\))([^A-Z]|$)/.test(role) || compact === 'FW') return 'fw'
  if (/(WF|LW|RW|LWF|RWF|AML|AMR|FWL|FWR|SS)/.test(compact) || /(^|[^A-Z])AM([^A-Z]|$)/.test(role)) return 'am'
  if (/(DM|CM|MID|MCL|MCR|LCM|RCM|LCMF|RCMF|MC|MF)/.test(compact)) return 'mid'
  if (/(CB|LCB|RCB|DCL|DCR|DC|D\(C\)|D\(CL\)|D\(CR\)|FB|WB|LB|RB|DL|DR|D\(L\)|D\(R\)|DEF)/.test(role) || compact.startsWith('D')) return 'def'
  return 'mid'
}

function lineupSideWeight(player: AnyRecord): number {
  const role = lineupRoleText(player)
  const compact = compactLineupRole(player)

  if (/(LWB|LB|DL|D\(L\)|LEFTBACK|WBL)/.test(role) || compact === 'DL') return 0
  if (/(LCB|DCL|D\(CL\)|DLC|LEFTCENTREBACK|LEFTCENTERBACK)/.test(role) || compact === 'DCL') return 1
  if (/(CB|DC|D\(C\)|CENTREBACK|CENTERBACK)/.test(role) || compact === 'DC') return 2
  if (/(RCB|DCR|D\(CR\)|DRC|RIGHTCENTREBACK|RIGHTCENTERBACK)/.test(role) || compact === 'DCR') return 3
  if (/(RWB|RB|DR|D\(R\)|RIGHTBACK|WBR)/.test(role) || compact === 'DR') return 4

  if (/(LW|LM|AML|FWL|LWF|LEFT)/.test(role)) return 0
  if (/(RW|RM|AMR|FWR|RWF|RIGHT)/.test(role)) return 4
  if (/(LCM|LCMF|MCL|LDM|DML)/.test(role)) return 1
  if (/(RCM|RCMF|MCR|RDM|DMR)/.test(role)) return 3
  return 2
}

function lineupBaseX(line: ReturnType<typeof lineupLineKey>, side: Side): number {
  const homeX = line === 'gk' ? 9 : line === 'def' ? 25 : line === 'mid' ? 46 : line === 'am' ? 66 : 84
  return side === 'home' ? homeX : 100 - homeX
}

function lineupPitchRows(players: AnyRecord[], side: Side) {
  const groups: Record<ReturnType<typeof lineupLineKey>, AnyRecord[]> = {
    gk: [],
    def: [],
    mid: [],
    am: [],
    fw: [],
  }

  players.forEach((player) => groups[lineupLineKey(player)].push(player))

  return (Object.keys(groups) as ReturnType<typeof lineupLineKey>[]).flatMap((line) => {
    const rows = groups[line].slice().sort((a, bPlayer) => lineupSideWeight(a) - lineupSideWeight(bPlayer) || s(a.player).localeCompare(s(bPlayer.player)))
    return rows.map((player, index) => ({
      player,
      x: lineupBaseX(line, side),
      y: 14 + ((index + 1) * 72) / (rows.length + 1),
      line,
    }))
  })
}

function LineupPitchGraphic({ side, players, tone }: { side: Side; players: AnyRecord[]; tone: 'cyan' | 'violet' }) {
  const rows = lineupPitchRows(players, side)
  const primary = tone === 'cyan' ? '#2dd8e9' : '#a78bfa'
  const soft = tone === 'cyan' ? 'rgba(45,216,233,0.22)' : 'rgba(167,139,250,0.22)'

  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 16, overflow: 'hidden', background: 'rgba(2,6,23,0.28)' }}>
      <PitchCanvas height={260} flip={side === 'away'} style={{ background: 'rgba(2,6,23,0.18)' }}>
        {rows.length ? (
          <g>
            {rows.map(({ player, x, y }, index) => {
              const px = pitchX(x)
              const py = pitchY(y)
              const shirt = getPlayerNodeLabel(player)
              const label = playerShortName(player.player)
              const isSub = b(player.is_substitute)
              const fill = isSub ? '#f59e0b' : primary
              const halo = isSub ? 'rgba(245,158,11,0.24)' : soft
              const stroke = isSub ? 'rgba(251,191,36,0.95)' : 'rgba(255,255,255,0.72)'

              return (
                <g key={`${s(player.player, String(index))}-${index}`}>
                  <circle cx={px} cy={py} r="4.9" fill={halo} stroke="rgba(255,255,255,0.20)" strokeWidth="0.45" />
                  <circle cx={px} cy={py} r="3.75" fill={fill} opacity="0.92" stroke={stroke} strokeWidth="0.65" />
                  {shirt && <text x={px} y={py + 1.05} textAnchor="middle" fill="rgba(2,6,23,0.96)" fontSize="2.65" fontWeight="950">{shirt}</text>}
                  <text x={px} y={py + 7.1} textAnchor="middle" fill="rgba(232,234,240,0.92)" fontSize="3.05" fontWeight="900">{label}</text>
                  <title>{`${s(player.player, 'Unknown')} · ${s(player.position, 'position not saved')} · ${isSub ? 'substitute' : 'starter'}`}</title>
                </g>
              )
            })}
          </g>
        ) : (
          <EmptyPitchNote label="No starting lineup rows available." />
        )}
      </PitchCanvas>
    </div>
  )
}

function MatchSetupPlayerRow({ player }: { player: AnyRecord }) {
  const cards = objectFromRecord(player, 'cards')
  const yellowCards = n(cards.yellow)
  const redCards = n(cards.red)
  const goals = n(player.goals)
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) auto', gap: 8, alignItems: 'center', padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s(player.player, 'Unknown')}</div>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 4 }}>
          {s(player.shirt_no) && <span style={inlineChipStyle('slate')}>#{s(player.shirt_no)}</span>}
          {s(player.position) && <span style={inlineChipStyle('cyan')}>{s(player.position)}</span>}
          {s(player.position_group) && <span style={inlineChipStyle('slate')}>{s(player.position_group)}</span>}
          {b(player.is_substitute) && <span style={inlineChipStyle('amber')}>Sub</span>}
          {player.mins_played !== null && player.mins_played !== undefined && <span style={inlineChipStyle('slate')}>{n(player.mins_played)} min</span>}
          {player.substitution_minute !== null && player.substitution_minute !== undefined && <span style={inlineChipStyle('amber')}>On {n(player.substitution_minute).toFixed(0)}'</span>}
          {goals > 0 && <span style={inlineChipStyle('cyan')}>{goals} G</span>}
          {yellowCards > 0 && <span style={inlineChipStyle('amber')}>{yellowCards} YC</span>}
          {redCards > 0 && <span style={{ ...inlineChipStyle('amber'), color: '#fb7185', borderColor: '#fb7185', background: 'rgba(251,113,133,0.14)' }}>{redCards} RC</span>}
        </div>
      </div>
      <div style={{ ...labelStyle(), color: b(player.is_starter) ? '#2dd8e9' : '#f59e0b' }}>{b(player.is_starter) ? 'Starter' : 'Bench'}</div>
    </div>
  )
}

function MatchSetupEventList({ title, rows, emptyLabel }: { title: string; rows: AnyRecord[]; emptyLabel: string }) {
  return (
    <div style={{ marginTop: 14 }}>
      <div style={{ ...labelStyle(), marginBottom: 4 }}>{title}</div>
      {rows.length ? (
        <div style={{ display: 'grid', gap: 6 }}>
          {rows.map((row, index) => {
            const minute = row.minute !== null && row.minute !== undefined ? `${n(row.minute).toFixed(1)}'` : 'Time not saved'
            const eventLabel = [s(row.player, 'Unknown'), s(row.type), s(row.outcome_type), s(row.card_type)].map(cleanVisibleText).filter(Boolean).join(' · ')
            return (
              <div key={`${title}-${s(row.event_index, String(index))}-${index}`} style={{ display: 'grid', gridTemplateColumns: '70px minmax(0, 1fr)', gap: 8, padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                <div style={{ ...inlineChipStyle('slate'), justifyContent: 'center' }}>{minute}</div>
                <div style={{ minWidth: 0, fontSize: 12, fontWeight: 850, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{eventLabel || 'Event saved without detail'}</div>
              </div>
            )
          })}
        </div>
      ) : (
        <div style={smallInfoStyle()}>{emptyLabel}</div>
      )}
    </div>
  )
}

function MatchSetupTeamCard({ side, data }: { side: Side; data: AnyRecord }) {
  const starters = listFromRecord(data, 'starting_xi')
  const bench = listFromRecord(data, 'bench')
  const goals = listFromRecord(data, 'goals')
  const cards = listFromRecord(data, 'cards')
  const substitutions = listFromRecord(data, 'substitutions')
  const tone = side === 'home' ? 'cyan' : 'violet'
  return (
    <div style={panelStyle({ padding: 16, minWidth: 0 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <h3 style={titleStyle()}>{s(data.team, side === 'home' ? 'Home' : 'Away')}</h3>
          <div style={{ ...smallInfoStyle(), marginTop: 4 }}>Formation {s(data.formation, 'not saved')}</div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <span style={inlineChipStyle(tone)}>{starters.length} starters</span>
          <span style={inlineChipStyle('slate')}>{bench.length} bench</span>
          <span style={inlineChipStyle('cyan')}>{goals.length} goals</span>
          <span style={inlineChipStyle('amber')}>{cards.length} cards</span>
          <span style={inlineChipStyle('violet')}>{substitutions.length} subs</span>
        </div>
      </div>
      <LineupPitchGraphic side={side} players={starters} tone={tone} />
      <div style={{ ...labelStyle(), marginTop: 12, marginBottom: 4 }}>Starting XI</div>
      <div style={{ display: 'grid', gap: 0 }}>{starters.length ? starters.map((player, index) => <MatchSetupPlayerRow key={`${s(player.player)}-${index}`} player={player} />) : <div style={smallInfoStyle()}>No starting lineup rows available.</div>}</div>
      <div style={{ ...labelStyle(), marginTop: 14, marginBottom: 4 }}>Bench</div>
      <div style={{ maxHeight: 210, overflow: 'auto', paddingRight: 4 }}>{bench.length ? bench.map((player, index) => <MatchSetupPlayerRow key={`${s(player.player)}-${index}`} player={player} />) : <div style={smallInfoStyle()}>No bench rows available.</div>}</div>
      <MatchSetupEventList title="Substitutions" rows={substitutions} emptyLabel="No substitution rows available." />
      <MatchSetupEventList title="Goals" rows={goals} emptyLabel="No goal rows available." />
      <MatchSetupEventList title="Cards" rows={cards} emptyLabel="No card rows available." />
      <ConfidenceNotes notes={data.confidence_notes} />
    </div>
  )
}

function MatchSetupPanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const setup = objectFromRecord((analysis as unknown as AnyRecord), 'match_setup')
  const homeSetup = objectFromRecord(setup, 'home')
  const awaySetup = objectFromRecord(setup, 'away')
  const fixture = analysis.selected_fixture
  const homeTeam = s(homeSetup.team, fixture?.home_team ?? 'Home')
  const awayTeam = s(awaySetup.team, fixture?.away_team ?? 'Away')
  const scoreline = fixture?.home_score === null || fixture?.away_score === null || fixture?.home_score === undefined || fixture?.away_score === undefined
    ? 'Score unavailable'
    : `${fixture.home_score}:${fixture.away_score}`
  const homeFormation = cleanVisibleText(homeSetup.formation)
  const awayFormation = cleanVisibleText(awaySetup.formation)
  const formationSummary = homeFormation || awayFormation ? `${homeFormation || 'Not saved'} v ${awayFormation || 'Not saved'}` : 'Formation not saved'
  const setupNotes = [
    ...listFromRecord(homeSetup, 'confidence_notes').map((item) => s(item)).filter(Boolean),
    ...listFromRecord(awaySetup, 'confidence_notes').map((item) => s(item)).filter(Boolean),
  ]
  const setupConfidence = setupNotes[0] ?? 'No setup confidence note available.'

  return (
    <section style={{ display: 'grid', gap: 12, marginTop: 16 }}>
      <div style={panelStyle({ padding: 16, boxShadow: 'none' })}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={titleStyle()}>Match context</h2>
            <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Compact setup view for report reading. Team sheets and event detail sit inside the expander below.</div>
          </div>
          <span style={inlineChipStyle('slate')}>{scoreline}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginTop: 14 }}>
          <MetricCard label="Home team" value={homeTeam} />
          <MetricCard label="Away team" value={awayTeam} />
          <MetricCard label="Score" value={scoreline} />
          <MetricCard label="Formation summary" value={formationSummary} />
        </div>
        <div style={{ ...smallInfoStyle(), marginTop: 10 }}>{setupConfidence}</div>
      </div>

      <details style={panelStyle({ padding: 16, boxShadow: 'none' })}>
        <summary style={{ cursor: 'pointer', fontWeight: 950, fontSize: 15 }}>Detailed team sheets and match context</summary>
        <div style={{ ...smallInfoStyle(), marginTop: 8 }}>Starting XI, bench, substitutions, goals, cards, formations and provider confidence notes.</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16, marginTop: 14 }}>
          <MatchSetupTeamCard side="home" data={homeSetup} />
          <MatchSetupTeamCard side="away" data={awaySetup} />
        </div>
      </details>
    </section>
  )
}


function playerEvidenceText(player: AnyRecord | undefined): string {
  if (!player) return 'Not enough event evidence'
  const reasons = Array.isArray(player.reasons) ? player.reasons.map((item) => s(item)).filter(Boolean) : []
  return reasons.length ? reasons.slice(0, 3).join(' · ') : 'Event based signal only'
}

function firstPlayerByScore(rows: AnyRecord[], scoreKey: string): AnyRecord | undefined {
  const scored = rows.filter((row) => cleanVisibleText(row.player) && n(row[scoreKey]) > 0)
  return scored[0] ?? rows.find((row) => cleanVisibleText(row.player))
}

function firstProgressor(rows: AnyRecord[]): AnyRecord | undefined {
  const scored = rows
    .filter((row) => cleanVisibleText(row.player))
    .slice()
    .sort((a, bPlayer) => {
      const aScore = (n(a.xt) * 12) + n(a.final_third_entries) + n(a.box_entries) + (n(a.carries) * 0.35) + (n(a.take_ons) * 0.35)
      const bScore = (n(bPlayer.xt) * 12) + n(bPlayer.final_third_entries) + n(bPlayer.box_entries) + (n(bPlayer.carries) * 0.35) + (n(bPlayer.take_ons) * 0.35)
      return bScore - aScore
    })
  return scored[0]
}

function InfluenceRow({ label, player, scoreKey }: { label: string; player: AnyRecord | undefined; scoreKey?: string }) {
  const playerName = cleanVisibleText(player?.player)
  const score = scoreKey ? n(player?.[scoreKey]) : 0
  return (
    <div style={{ display: 'grid', gap: 4, padding: '9px 0', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
      <div style={labelStyle()}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 950 }}>{playerName || 'Not enough event evidence'}</div>
      <div style={smallInfoStyle()}>{playerName ? playerEvidenceText(player) : 'Not enough event evidence'}</div>
      {playerName && scoreKey && <div style={{ ...smallInfoStyle(), fontSize: 11 }}>Score: {score.toFixed(1)}</div>}
    </div>
  )
}

function PlayerInfluenceTeamCard({ name, data }: { name: string; data: AnyRecord }) {
  const attacking = listFromRecord(data, 'attacking')
  const defensive = listFromRecord(data, 'defensive')
  const setPieces = listFromRecord(data, 'set_pieces')
  const overall = listFromRecord(data, 'overall')
  const transitions = listFromRecord(data, 'transitions')
  const threatCreator = firstPlayerByScore(attacking, 'attacking_score')
  const progressor = firstProgressor(attacking) ?? firstPlayerByScore(transitions, 'transition_score')
  const defensiveInfluence = firstPlayerByScore(defensive, 'defensive_score')
  const setPieceInfluence = firstPlayerByScore(setPieces, 'set_piece_score')
  const keyPlayer = firstPlayerByScore(overall, 'overall_score')

  return (
    <div style={panelStyle({ padding: 16, minWidth: 0 })}>
      <h3 style={titleStyle()}>{name}</h3>
      <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Role influence is taken from the current best players analysis. It is an event based guide, not a video grade.</div>
      <div style={{ display: 'grid', gap: 0, marginTop: 10 }}>
        <InfluenceRow label="Main threat creator" player={threatCreator} scoreKey="attacking_score" />
        <InfluenceRow label="Main ball progressor" player={progressor} scoreKey={progressor && n(progressor.xt) > 0 ? 'xt' : 'transition_score'} />
        <InfluenceRow label="Main defensive influence" player={defensiveInfluence} scoreKey="defensive_score" />
        <InfluenceRow label="Set piece influence" player={setPieceInfluence} scoreKey="set_piece_score" />
        <InfluenceRow label="Overall key player" player={keyPlayer} scoreKey="overall_score" />
      </div>
    </div>
  )
}

function PlayerInfluenceSummary({ analysis }: { analysis: MatchAnalysisResponse }) {
  const best = objectFromRecord((analysis as unknown as AnyRecord), 'best_players_analysis')
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'

  return (
    <DashboardSection title="Player influence summary" note="Quick event based read on who shaped the match before opening the full best players tab.">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16, marginTop: 12 }}>
        <PlayerInfluenceTeamCard name={homeName} data={objectFromRecord(best, 'home')} />
        <PlayerInfluenceTeamCard name={awayName} data={objectFromRecord(best, 'away')} />
      </div>
    </DashboardSection>
  )
}

function metricLeader(home: AnyRecord | undefined, away: AnyRecord | undefined, homeName: string, awayName: string, key: string): { name: string; value: number; opponent: string } {
  const homeValue = n(home?.[key])
  const awayValue = n(away?.[key])
  return homeValue >= awayValue ? { name: homeName, value: homeValue, opponent: awayName } : { name: awayName, value: awayValue, opponent: homeName }
}

function routeLabel(summary: AnyRecord | undefined): string {
  const routes = [
    { label: 'box entries', value: n(summary?.box_entries) },
    { label: 'final third entries', value: n(summary?.final_third_entries) },
    { label: 'wide delivery', value: n(summary?.crosses) },
    { label: 'ball carrying', value: n(summary?.progressive_carries) + n(summary?.carry_final_third_entries) },
    { label: 'set pieces', value: n(summary?.set_piece_actions) + n(summary?.corners) + n(summary?.free_kicks) },
  ]
  routes.sort((a, bRoute) => bRoute.value - a.value)
  return routes[0]?.value > 0 ? `${routes[0].label} (${routes[0].value})` : 'no clear route in the event data'
}

function transitionSummaryForSide(analysis: MatchAnalysisResponse, side: Side): AnyRecord {
  const transitions = objectFromRecord((analysis as unknown as AnyRecord), 'transition_analysis')
  return objectFromRecord(objectFromRecord(transitions, side), 'summary')
}

function PhaseVerdictCard({ title, lines }: { title: string; lines: Array<{ label: string; value: string }> }) {
  return (
    <div style={panelStyle({ padding: 16, boxShadow: 'none', minWidth: 0 })}>
      <h3 style={{ ...titleStyle(), fontSize: 15 }}>{title}</h3>
      <div style={{ display: 'grid', gap: 8, marginTop: 10 }}>
        {lines.map((line) => (
          <div key={`${title}-${line.label}`} style={{ display: 'grid', gap: 2 }}>
            <div style={labelStyle()}>{line.label}</div>
            <div style={{ fontSize: 12, color: 'rgba(226,232,240,0.9)', lineHeight: 1.45 }}>{line.value}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function PhaseVerdicts({ analysis }: { analysis: MatchAnalysisResponse }) {
  const home = analysis.team_summaries?.home as AnyRecord | undefined
  const away = analysis.team_summaries?.away as AnyRecord | undefined
  const homeName = s(home?.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayName = s(away?.team, analysis.selected_fixture?.away_team ?? 'Away')
  const attackingLeader = metricLeader(home, away, homeName, awayName, 'box_entries')
  const defensiveLeader = metricLeader(home, away, homeName, awayName, 'defensive_actions')
  const transitionHome = transitionSummaryForSide(analysis, 'home')
  const transitionAway = transitionSummaryForSide(analysis, 'away')
  const transitionLeader = n(transitionHome.counter_attacks) >= n(transitionAway.counter_attacks)
    ? { name: homeName, value: n(transitionHome.counter_attacks), risk: n(transitionHome.counter_attacks_against), opponent: awayName }
    : { name: awayName, value: n(transitionAway.counter_attacks), risk: n(transitionAway.counter_attacks_against), opponent: homeName }
  const transitionRisk = n(transitionHome.counter_attacks_against) >= n(transitionAway.counter_attacks_against)
    ? `${homeName} allowed ${n(transitionHome.counter_attacks_against)} opponent transition attacks after losses.`
    : `${awayName} allowed ${n(transitionAway.counter_attacks_against)} opponent transition attacks after losses.`
  const setPieceLeader = metricLeader(home, away, homeName, awayName, 'set_piece_shots')

  return (
    <div style={{ display: 'grid', gap: 12, marginBottom: 16 }}>
      <div>
        <h3 style={{ ...titleStyle(), fontSize: 16 }}>Phase verdicts</h3>
        <div style={{ ...smallInfoStyle(), marginTop: 5 }}>Professional first read from event data. Each point still needs video confirmation before it becomes a final tactical conclusion.</div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
        <PhaseVerdictCard
          title="Attacking verdict"
          lines={[
            { label: 'Strongest route', value: `${attackingLeader.name} carried the clearer attacking entry signal, led by ${routeLabel(attackingLeader.name === homeName ? home : away)}.` },
            { label: 'Main risk', value: 'The event data shows where attacks finished, but not whether the route was planned, forced, or created by broken play.' },
            { label: 'Repeatability', value: 'Repeatability is stronger if the same lane, player connection, and entry type appears across multiple possessions on video.' },
            { label: 'What to check on video', value: 'Check whether the threat came from stable patterns, individual actions, set plays, or loose second balls.' },
          ]}
        />
        <PhaseVerdictCard
          title="Defensive verdict"
          lines={[
            { label: 'Strongest route', value: `${defensiveLeader.name} logged the higher defensive action volume with ${defensiveLeader.value} actions.` },
            { label: 'Main risk', value: 'Defensive height and compactness cannot be proven from event locations alone.' },
            { label: 'Repeatability', value: 'The signal is repeatable only if the team shape and pressing triggers match the event locations on video.' },
            { label: 'What to check on video', value: 'Check block height, central protection, pressure timing, and whether actions happened after danger had already arrived.' },
          ]}
        />
        <PhaseVerdictCard
          title="Transition verdict"
          lines={[
            { label: 'Strongest route', value: `${transitionLeader.name} produced ${transitionLeader.value} regain to danger sequences in the current event sample.` },
            { label: 'Main risk', value: transitionRisk },
            { label: 'Repeatability', value: 'Transition numbers need match state context because late chasing phases and scoreline can inflate exposure.' },
            { label: 'What to check on video', value: 'Check whether exposure comes from poor rest defence, risky passing, or simply the game state.' },
          ]}
        />
        <PhaseVerdictCard
          title="Set piece verdict"
          lines={[
            { label: 'Strongest route', value: `${setPieceLeader.name} had the stronger set piece shot signal with ${setPieceLeader.value} set piece shots.` },
            { label: 'Main risk', value: 'Set piece danger can come from delivery, first contact, second balls, or poor clearance structure. The event feed does not separate that fully.' },
            { label: 'Repeatability', value: 'The routine is repeatable only if the same delivery zone and target behaviour appears across several restarts.' },
            { label: 'What to check on video', value: 'Check delivery quality, blocking, first contact, second ball positioning, and defensive marking assignments.' },
          ]}
        />
      </div>
    </div>
  )
}

function VideoChecksRequired() {
  const checks = [
    'Check whether the pass network reflects true structure or only event locations.',
    'Check whether defensive height reflects team block height or only defensive action locations.',
    'Check whether attacking threat came from repeatable patterns or broken play.',
    'Check whether substitutions changed role behaviour.',
    'Check whether set piece danger came from delivery quality, first contact, or second balls.',
    'Check whether transition exposure reflects poor rest defence or simply match state.',
  ]

  return (
    <DashboardSection title="Video checks required" note="Use these checks before turning the dashboard into a final report conclusion.">
      <div style={panelStyle({ padding: 16, marginTop: 12 })}>
        <div style={{ display: 'grid', gap: 8 }}>
          {checks.map((check, index) => (
            <div key={check} style={{ display: 'grid', gridTemplateColumns: '26px minmax(0, 1fr)', gap: 8, alignItems: 'start' }}>
              <span style={{ ...inlineChipStyle('slate'), justifyContent: 'center' }}>{index + 1}</span>
              <span style={{ fontSize: 12, color: 'rgba(226,232,240,0.9)', lineHeight: 1.45 }}>{check}</span>
            </div>
          ))}
        </div>
      </div>
    </DashboardSection>
  )
}

function GameStateFilterPanel({ analysis, gameState, perspective, onGameStateChange, onPerspectiveChange }: { analysis: MatchAnalysisResponse; gameState: GameStateFilter; perspective: PerspectiveFilter; onGameStateChange: (value: GameStateFilter) => void; onPerspectiveChange: (value: PerspectiveFilter) => void }) {
  const activeFilter = objectFromRecord((analysis as unknown as AnyRecord), 'active_filter')
  const before = n(activeFilter.event_count_before)
  const after = n(activeFilter.event_count_after)
  return (
    <section style={panelStyle({ padding: 12, marginTop: 12, borderRadius: 16, boxShadow: '0 12px 32px rgba(0,0,0,0.18)' })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 950 }}>Game state filter</div>
          <div style={smallInfoStyle()}>Changing this reloads the backend analysis, so cards and charts are rebuilt from the selected state.</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <label><span style={labelStyle()}>State</span><select value={gameState} onChange={(event) => onGameStateChange(event.currentTarget.value as GameStateFilter)} style={{ ...FIELD_STYLE, width: 220 }}>{GAME_STATE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label><span style={labelStyle()}>Perspective</span><select value={perspective} onChange={(event) => onPerspectiveChange(event.currentTarget.value as PerspectiveFilter)} style={{ ...FIELD_STYLE, width: 180 }}><option value="home">Home perspective</option><option value="away">Away perspective</option></select></label>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 10 }}>
        <span style={inlineChipStyle('slate')}>Events before {before}</span>
        <span style={inlineChipStyle(after < before ? 'amber' : 'cyan')}>Events after {after}</span>
      </div>
      <ConfidenceNotes notes={activeFilter.notes} />
    </section>
  )
}

function TransitionSummaryCards({ sideData }: { sideData: AnyRecord }) {
  const summary = objectFromRecord(sideData, 'summary')
  const cards = [
    ['Regains', summary.regains],
    ['High regains', summary.high_regains],
    ['Regains to shot 15s', summary.regains_leading_to_shot_15s],
    ['Regains to box 15s', summary.regains_leading_to_box_entry_15s],
    ['Losses', summary.losses],
    ['Losses to shot 15s', summary.losses_leading_to_opponent_shot_15s],
    ['Losses to box 15s', summary.losses_leading_to_opponent_box_entry_15s],
    ['Counters', summary.counter_attacks],
    ['Counters against', summary.counter_attacks_against],
  ]
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>{cards.map(([label, value]) => <MetricCard key={s(label)} label={s(label)} value={n(value)} />)}</div>
}

function CompactRowsTable({ rows, columns, emptyLabel }: { rows: AnyRecord[]; columns: Array<{ key: string; label: string; render?: (row: AnyRecord) => string | number }>; emptyLabel: string }) {
  if (!rows.length) return <div style={smallInfoStyle()}>{emptyLabel}</div>
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead><tr>{columns.map((column) => <th key={column.key} style={{ textAlign: 'left', color: 'var(--muted)', padding: '7px 6px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>{column.label}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={`${s(row.event_index, String(index))}-${index}`}>{columns.map((column) => <td key={column.key} style={{ padding: '7px 6px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>{column.render ? column.render(row) : s(row[column.key])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function TransitionTeamPanel({ title, data, tone }: { title: string; data: AnyRecord; tone: 'cyan' | 'violet' }) {
  const maps = objectFromRecord(data, 'maps')
  const topPlayers = listFromRecord(data, 'top_transition_players')
  const worstLosses = listFromRecord(data, 'worst_losses')
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      <div style={panelStyle({ padding: 16 })}>
        <h3 style={titleStyle()}>{title}</h3>
        <div style={{ ...smallInfoStyle(), margin: '5px 0 12px' }}>Regain and loss outcomes within the next 15 seconds.</div>
        <TransitionSummaryCards sideData={data} />
        <ConfidenceNotes notes={data.confidence_notes} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: 12 }}>
        <ArrowPitchMap title={`${title} regains to danger`} note="Regain location connected to the next shot or box entry." arrows={listFromRecord(maps, 'regain_to_danger')} tone={tone} />
        <ArrowPitchMap title={`${title} losses to danger`} note="Loss location connected to the opponent response." arrows={listFromRecord(maps, 'loss_to_danger')} tone="amber" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
        <div style={panelStyle({ padding: 16 })}>
          <h3 style={titleStyle()}>Top transition players</h3>
          <CompactRowsTable rows={topPlayers} emptyLabel="No transition player rows available." columns={[{ key: 'player', label: 'Player' }, { key: 'regains', label: 'Regains', render: (row) => n(row.regains) }, { key: 'high_regains', label: 'High', render: (row) => n(row.high_regains) }, { key: 'losses', label: 'Losses', render: (row) => n(row.losses) }]} />
        </div>
        <div style={panelStyle({ padding: 16 })}>
          <h3 style={titleStyle()}>Worst losses</h3>
          <CompactRowsTable rows={worstLosses} emptyLabel="No loss to danger rows available." columns={[{ key: 'minute', label: 'Min', render: (row) => row.minute !== null && row.minute !== undefined ? n(row.minute).toFixed(1) : '' }, { key: 'player', label: 'Player' }, { key: 'danger_type', label: 'Danger' }, { key: 'seconds_to_danger', label: 'Secs', render: (row) => row.seconds_to_danger !== null && row.seconds_to_danger !== undefined ? n(row.seconds_to_danger).toFixed(1) : '' }]} />
        </div>
      </div>
    </div>
  )
}

function TransitionsTab({ analysis }: { analysis: MatchAnalysisResponse }) {
  const transitions = objectFromRecord((analysis as unknown as AnyRecord), 'transition_analysis')
  const home = objectFromRecord(transitions, 'home')
  const away = objectFromRecord(transitions, 'away')
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={panelStyle({ padding: 16 })}>
        <h3 style={titleStyle()}>Transition analysis</h3>
        <div style={{ ...smallInfoStyle(), marginTop: 5 }}>This reads event based transition exposure. It does not claim the full team structure without tracking context.</div>
        <ConfidenceNotes notes={transitions.confidence_notes} />
      </div>
      <TransitionTeamPanel title={s(home.team, analysis.selected_fixture?.home_team ?? 'Home')} data={home} tone="cyan" />
      <TransitionTeamPanel title={s(away.team, analysis.selected_fixture?.away_team ?? 'Away')} data={away} tone="violet" />
    </div>
  )
}

function PossessionChainsViz({ analysis }: { analysis: MatchAnalysisResponse }) {
  const chainsRoot = objectFromRecord((analysis as unknown as AnyRecord), 'possession_chains')
  const [side, setSide] = useState<Side>('home')
  const [category, setCategory] = useState('best_attacking_chains')
  const [selectedId, setSelectedId] = useState('')
  const [isPlaying, setIsPlaying] = useState(false)
  const [playIndex, setPlayIndex] = useState(0)
  const sideData = objectFromRecord(chainsRoot, side)
  const chains = listFromRecord(sideData, category)
  const selectedChain = chains.find((chain) => s(chain.chain_id) === selectedId) ?? chains[0]
  const actions = selectedChain ? listFromRecord(selectedChain, 'actions') : []
  const teamName = s(sideData.team, side === 'home' ? analysis.selected_fixture?.home_team ?? 'Home' : analysis.selected_fixture?.away_team ?? 'Away')
  const activeAction = actions[Math.max(0, Math.min(actions.length - 1, playIndex))] as AnyRecord | undefined
  const playLabel = !actions.length ? 'No sequence' : isPlaying ? 'Pause' : playIndex >= actions.length - 1 && playIndex > 0 ? 'Replay' : 'Play'

  useEffect(() => { setSelectedId('') }, [side, category, analysis])
  useEffect(() => { setIsPlaying(false); setPlayIndex(0) }, [selectedChain?.chain_id, side, category, analysis])

  useEffect(() => {
    if (!isPlaying || actions.length <= 1) return undefined
    const timer = window.setInterval(() => {
      setPlayIndex((current) => {
        if (current >= actions.length - 1) {
          window.clearInterval(timer)
          setIsPlaying(false)
          return current
        }
        return current + 1
      })
    }, 720)
    return () => window.clearInterval(timer)
  }, [isPlaying, actions.length])

  const handlePlayClick = () => {
    if (!actions.length) return
    if (actions.length <= 1) {
      setPlayIndex(0)
      setIsPlaying(false)
      return
    }
    if (isPlaying) {
      setIsPlaying(false)
      return
    }
    if (playIndex >= actions.length - 1) setPlayIndex(0)
    setIsPlaying(true)
  }

  const revealIndex = isPlaying || playIndex > 0 ? playIndex : null

  return (
    <div style={panelStyle({ padding: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 12 }}>
        <div><h3 style={titleStyle()}>Possession chains</h3><div style={smallInfoStyle()}>Separate sequence view using numbered arrows. This is not the pass network. Hover over actions, lines and the ball for details.</div></div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => setSide('home')} style={buttonStyle(side === 'home')}>Home</button>
          <button type="button" onClick={() => setSide('away')} style={buttonStyle(side === 'away')}>Away</button>
          <select value={category} onChange={(event) => setCategory(event.currentTarget.value)} style={{ ...FIELD_STYLE, width: 230, marginTop: 0 }}>{CHAIN_CATEGORIES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
        </div>
      </div>
      <div style={{ ...smallInfoStyle(), marginBottom: 10 }}>{teamName} · {chains.length} chains in this category</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 0.8fr) minmax(360px, 1.2fr)', gap: 14 }}>
        <div style={{ maxHeight: 380, overflow: 'auto', display: 'grid', gap: 8, paddingRight: 4 }}>
          {chains.length ? chains.map((chain, index) => (
            <button
              key={`${s(chain.chain_id)}-${index}`}
              type="button"
              onClick={() => { setSelectedId(s(chain.chain_id)); setIsPlaying(false); setPlayIndex(0) }}
              title={`${s(chain.outcome_label, 'Chain')} from ${s(chain.start_minute, '0')}' to ${s(chain.end_minute, '')}'. ${n(chain.action_count)} actions, xG ${n(chain.xg).toFixed(2)}, xT ${n(chain.xt_added).toFixed(2)}.`}
              style={{ ...buttonStyle(s(selectedChain?.chain_id) === s(chain.chain_id)), textAlign: 'left', padding: 10 }}
            >
              <div style={{ fontSize: 12, fontWeight: 950 }}>{s(chain.outcome_label, 'Chain')} · {s(chain.start_minute, '0')}'</div>
              <div style={{ ...smallInfoStyle(), fontSize: 11, marginTop: 4 }}>{n(chain.action_count)} actions · {n(chain.duration_seconds).toFixed(0)}s · xG {n(chain.xg).toFixed(2)} · xT {n(chain.xt_added).toFixed(2)}</div>
            </button>
          )) : <div style={smallInfoStyle()}>No possession chains available for this category.</div>}
        </div>
        <div>
          {selectedChain && (
            <div style={{ display: 'grid', gap: 9, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}><span style={inlineChipStyle('slate')}>Start {s(selectedChain.start_minute)}'</span><span style={inlineChipStyle('slate')}>{n(selectedChain.duration_seconds).toFixed(0)}s</span><span style={inlineChipStyle('cyan')}>{n(selectedChain.action_count)} actions</span><span style={inlineChipStyle('violet')}>xG {n(selectedChain.xg).toFixed(2)}</span><span style={inlineChipStyle('amber')}>xT {n(selectedChain.xt_added).toFixed(2)}</span><span style={inlineChipStyle('slate')}>{s(selectedChain.outcome_label)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <button type="button" onClick={handlePlayClick} disabled={!actions.length} style={{ ...buttonStyle(isPlaying), opacity: actions.length ? 1 : 0.55 }} title="Play this possession sequence with the ball moving from action to action.">{isPlaying ? 'Pause sequence' : playLabel}</button>
                <div style={{ ...smallInfoStyle(), textAlign: 'right' }}>{actions.length ? `Action ${Math.min(playIndex + 1, actions.length)} of ${actions.length}${activeAction ? ` · ${s(activeAction.player)} ${s(activeAction.type, s(activeAction.event_type))}` : ''}` : 'No actions to play'}</div>
              </div>
            </div>
          )}
          <PitchCanvas height={360}>{actions.length ? <PitchSequenceLayer actions={actions} tone={side === 'home' ? 'cyan' : 'violet'} revealIndex={revealIndex} activeIndex={playIndex} showBall={isPlaying || playIndex > 0} playing={isPlaying} /> : <EmptyPitchNote label="No sequence coordinates available." />}</PitchCanvas>
          <EventLegend tone={side === 'home' ? 'cyan' : 'violet'} items={['pass', 'carry_path', 'take_on_path', 'shot', 'goal']} />
        </div>
      </div>
      <ConfidenceNotes notes={sideData.confidence_notes ?? chainsRoot.confidence_notes} />
    </div>
  )
}


function PrintableVisualCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="print-visual-card">
      <h3>{title}</h3>
      {children}
    </div>
  )
}

function PrintableMomentumVisual({ analysis }: { analysis: MatchAnalysisResponse }) {
  const points = analysis.momentum ?? []
  const width = 760
  const height = 190
  const left = 34
  const right = 18
  const top = 18
  const bottom = 28
  const chartWidth = width - left - right
  const chartHeight = height - top - bottom
  const maxMinute = Math.max(90, ...points.map((point) => n(point.minute)))
  const maxValue = Math.max(1, ...points.flatMap((point) => [n(point.home), n(point.away)]))
  const xFor = (minute: number) => left + (minute / maxMinute) * chartWidth
  const yFor = (value: number) => top + chartHeight - (value / maxValue) * chartHeight
  const homeLine = points.map((point) => `${xFor(n(point.minute)).toFixed(1)},${yFor(n(point.home)).toFixed(1)}`).join(' ')
  const awayLine = points.map((point) => `${xFor(n(point.minute)).toFixed(1)},${yFor(n(point.away)).toFixed(1)}`).join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 180, display: 'block' }}>
      <rect x={left} y={top} width={chartWidth} height={chartHeight} rx="10" fill="#f8fafc" stroke="#d1d5db" />
      {[0, 15, 30, 45, 60, 75, 90].map((minute) => (
        <g key={minute}>
          <line x1={xFor(minute)} x2={xFor(minute)} y1={top} y2={top + chartHeight} stroke="#e5e7eb" />
          <text x={xFor(minute)} y={height - 10} fill="#475569" fontSize="10" textAnchor="middle">{minute}'</text>
        </g>
      ))}
      <polyline points={homeLine} fill="none" stroke="#0891b2" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points={awayLine} fill="none" stroke="#7c3aed" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function PrintableRoutineSummary({ data }: { data: AnyRecord }) {
  const routines = listFromRecord(data, 'routine_groups').slice(0, 5)
  if (!routines.length) return <p>No routine groups available.</p>
  return (
    <table className="print-table">
      <thead><tr><th>Routine</th><th>Used</th><th>Shots</th><th>1st contact</th><th>Target</th></tr></thead>
      <tbody>
        {routines.map((row, index) => (
          <tr key={`${s(row.routine_key)}-${index}`}>
            <td>{s(row.routine_label)}</td>
            <td>{n(row.count)}</td>
            <td>{n(row.shots)}</td>
            <td>{n(row.first_contact_won)}</td>
            <td>{listFromRecord(row, 'top_target_zones').map((zone) => s(zone.zone)).slice(0, 2).join(', ')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}


function PrintableSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="print-section">
      <h2>{title}</h2>
      <div className="print-section-body">{children}</div>
    </section>
  )
}

function PrintableSideBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="print-side-block">
      <h3>{title}</h3>
      <div style={{ display: 'grid', gap: 12 }}>{children}</div>
    </div>
  )
}

function PrintableDefensiveFullSection({ analysis }: { analysis: MatchAnalysisResponse }) {
  const defensive = (analysis.defensive_analysis ?? {}) as Record<Side, AnyRecord>
  const teams: Array<{ side: Side; name: string }> = [
    { side: 'home', name: analysis.selected_fixture?.home_team ?? 'Home' },
    { side: 'away', name: analysis.selected_fixture?.away_team ?? 'Away' },
  ]

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {teams.map(({ side, name }) => {
        const data = objectFromRecord(defensive, side)
        if (!Object.keys(data).length) {
          return <PrintableSideBlock key={side} title={`${name} defensive analysis`}><p>No defensive payload available.</p></PrintableSideBlock>
        }
        return (
          <PrintableSideBlock key={side} title={`${name} defensive analysis`}>
            <DefensiveInterpretation data={data} />
            <DefensiveControlFunnel data={data} />
            <DuelControlPanel data={data} />
            <DefensiveProgressionMap data={data} />
            <LaneProtectionPanel data={data} />
            <DangerConcededPanel data={data} />
            <DefensiveBlockProfilePanel data={data} />
            <DefensiveDisruptionPanel data={data} />
            <PressingEffectPanel data={data} />
            <DefensiveSequencesPanel data={data} />
            <DefensiveLeaders data={data} />
            <DefensiveEventAudit data={data} />
          </PrintableSideBlock>
        )
      })}
    </div>
  )
}

function PrintableSetPiecesFullSection({ analysis }: { analysis: MatchAnalysisResponse }) {
  const setPieces = (analysis.set_piece_analysis ?? {}) as Record<Side, AnyRecord>
  const teams: Array<{ side: Side; name: string }> = [
    { side: 'home', name: analysis.selected_fixture?.home_team ?? 'Home' },
    { side: 'away', name: analysis.selected_fixture?.away_team ?? 'Away' },
  ]

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      {teams.map(({ side, name }) => {
        const data = objectFromRecord(setPieces, side)
        if (!Object.keys(data).length) {
          return <PrintableSideBlock key={side} title={`${name} set pieces`}><p>No set piece payload available.</p></PrintableSideBlock>
        }
        return (
          <PrintableSideBlock key={side} title={`${name} set pieces`}>
            <SetPieceInterpretation data={data} />
            <SetPieceSummaryPanel data={data} />
            <SetPieceRoutineGroupsPanel data={data} />
            <SetPieceDeliveryMap data={data} />
            <SetPieceFirstContactPanel data={data} />
            <SetPieceThreatPanel data={data} />
            <SetPieceSequencesPanel data={data} />
            <SetPieceInvolvementPanel data={data} />
          </PrintableSideBlock>
        )
      })}
    </div>
  )
}

function PrintableBestPlayersFullSection({ analysis }: { analysis: MatchAnalysisResponse }) {
  const best = objectFromRecord((analysis as unknown as AnyRecord), 'best_players_analysis')
  const categories: Array<{ key: 'overall' | 'attacking' | 'defensive' | 'transitions' | 'set_pieces'; label: string; scoreKey: string }> = [
    { key: 'overall', label: 'Overall', scoreKey: 'overall_score' },
    { key: 'attacking', label: 'Attacking', scoreKey: 'attacking_score' },
    { key: 'defensive', label: 'Defensive', scoreKey: 'defensive_score' },
    { key: 'transitions', label: 'Transitions', scoreKey: 'transition_score' },
    { key: 'set_pieces', label: 'Set pieces', scoreKey: 'set_piece_score' },
  ]
  const teams: Array<{ side: Side; name: string }> = [
    { side: 'home', name: analysis.selected_fixture?.home_team ?? 'Home' },
    { side: 'away', name: analysis.selected_fixture?.away_team ?? 'Away' },
  ]

  return (
    <div className="print-grid">
      {teams.map(({ side, name }) => {
        const sideData = objectFromRecord(best, side)
        return (
          <div key={side} className="print-visual-card">
            <h3>{name}</h3>
            {categories.map((category) => {
              const rows = listFromRecord(sideData, category.key).slice(0, 8)
              return (
                <div key={`${side}-${category.key}`} style={{ marginTop: 10 }}>
                  <h4>{category.label}</h4>
                  {rows.length ? (
                    <table className="print-table">
                      <thead><tr><th>Player</th><th>Score</th><th>Evidence</th></tr></thead>
                      <tbody>
                        {rows.map((row, index) => (
                          <tr key={`${side}-${category.key}-${s(row.player)}-${index}`}>
                            <td>{s(row.player)}</td>
                            <td>{n(row[category.scoreKey]).toFixed(1)}</td>
                            <td>{listFromRecord(row, 'reasons').map((item) => s(item)).filter(Boolean).slice(0, 2).join(' · ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p>No {category.label.toLowerCase()} player rows available.</p>
                  )}
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function PrintableRecentContextFullSection({ analysis }: { analysis: MatchAnalysisResponse }) {
  const patterns = analysis.recent_patterns ?? { home: { available: false }, away: { available: false } }
  const homeName = analysis.selected_fixture?.home_team ?? 'Home'
  const awayName = analysis.selected_fixture?.away_team ?? 'Away'

  return (
    <div className="print-grid">
      <RecentPatternCard title={`${homeName} recent attacking context`} pattern={objectFromRecord(patterns as AnyRecord, 'home')} />
      <RecentPatternCard title={`${awayName} recent attacking context`} pattern={objectFromRecord(patterns as AnyRecord, 'away')} />
    </div>
  )
}

function PrintableKpiCard({ label, homeLabel, awayLabel, homeValue, awayValue }: { label: string; homeLabel: string; awayLabel: string; homeValue: string; awayValue: string }) {
  return (
    <div className="print-kpi-card">
      <div className="print-kpi-label">{label}</div>
      <div className="print-kpi-values">
        <div><strong>{homeValue}</strong><span>{homeLabel}</span></div>
        <div><strong>{awayValue}</strong><span>{awayLabel}</span></div>
      </div>
    </div>
  )
}

function PrintableReportCover({ analysis }: { analysis: MatchAnalysisResponse }) {
  const fixture = analysis.selected_fixture
  const activeFilter = objectFromRecord((analysis as unknown as AnyRecord), 'active_filter')
  const home = (analysis.team_summaries?.home ?? {}) as AnyRecord
  const away = (analysis.team_summaries?.away ?? {}) as AnyRecord
  const homeTeam = s(home.team, fixture?.home_team ?? 'Home')
  const awayTeam = s(away.team, fixture?.away_team ?? 'Away')
  const reportDate = new Date().toLocaleString()
  const valueFor = (summary: AnyRecord, key: string, decimals = 0) => {
    const value = n(summary[key])
    return decimals > 0 ? value.toFixed(decimals) : String(Math.round(value))
  }

  return (
    <section className="print-cover">
      <div className="print-cover-topline">
        <span>Match Analysis Report</span>
        <span>{s((analysis as unknown as AnyRecord).nation)} {s((analysis as unknown as AnyRecord).tier)} · {s(analysis.season)}</span>
      </div>

      <div className="print-cover-main">
        <div>
          <div className="print-report-label">WhoScored event data analysis</div>
          <h1>{fixtureLabel(fixture as MatchFixture)}</h1>
          <p>
            {fixture ? formatKickoff(fixture.kickoff) : 'Kick off unavailable'} · Game state {s(activeFilter.game_state, 'all')} · Perspective {s(activeFilter.perspective, 'home')}
          </p>
        </div>
        <div className="print-score-card">
          <div className="print-score-team">{homeTeam}</div>
          <div className="print-scoreline">{fixture?.home_score ?? ' '} : {fixture?.away_score ?? ' '}</div>
          <div className="print-score-team">{awayTeam}</div>
        </div>
      </div>

      <div className="print-kpi-grid">
        <PrintableKpiCard label="Shots" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'shots')} awayValue={valueFor(away, 'shots')} />
        <PrintableKpiCard label="Shots on target" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'shots_on_target')} awayValue={valueFor(away, 'shots_on_target')} />
        <PrintableKpiCard label="xG" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'xg', 2)} awayValue={valueFor(away, 'xg', 2)} />
        <PrintableKpiCard label="Box entries" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'box_entries')} awayValue={valueFor(away, 'box_entries')} />
        <PrintableKpiCard label="High regains" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'high_regains')} awayValue={valueFor(away, 'high_regains')} />
        <PrintableKpiCard label="Defensive actions" homeLabel={homeTeam} awayLabel={awayTeam} homeValue={valueFor(home, 'defensive_actions')} awayValue={valueFor(away, 'defensive_actions')} />
      </div>

      <div className="print-report-map">
        <span>01 Momentum</span>
        <span>02 Match stats</span>
        <span>03 Lineups</span>
        <span>04 Team radar</span>
        <span>05 Attacking</span>
        <span>06 Defensive</span>
        <span>07 Transitions</span>
        <span>08 Set pieces</span>
        <span>09 Players</span>
        <span>10 Audit</span>
      </div>

      <div className="print-cover-footer">
        <span>Generated {reportDate}</span>
        <span>{n(activeFilter.event_count_after, analysis.event_count)} event rows in selected view</span>
      </div>
    </section>
  )
}

function ExportReportButton({ analysis, rawColumns, compact = false }: { analysis: MatchAnalysisResponse; rawColumns: string[]; compact?: boolean }) {
  const handleExport = () => {
    window.requestAnimationFrame(() => window.print())
  }

  if (compact) {
    return (
      <>
        <button type="button" onClick={handleExport} style={buttonStyle(true)}>Export readable PDF</button>
        <PrintableMatchReport analysis={analysis} rawColumns={rawColumns} />
      </>
    )
  }

  return (
    <section style={panelStyle({ padding: 14, marginTop: 16, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' })}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 950 }}>Export readable PDF report</div>
        <div style={smallInfoStyle()}>Creates a cleaner print layout with a cover page, compact match summary, stronger section breaks and lighter report styling for PDF export.</div>
      </div>
      <button type="button" onClick={handleExport} style={buttonStyle(true)}>Export readable PDF</button>
      <PrintableMatchReport analysis={analysis} rawColumns={rawColumns} />
    </section>
  )
}

function PrintableMatchReport({ analysis, rawColumns }: { analysis: MatchAnalysisResponse; rawColumns: string[] }) {
  const fixture = analysis.selected_fixture
  const activeFilter = objectFromRecord((analysis as unknown as AnyRecord), 'active_filter')
  const setup = objectFromRecord((analysis as unknown as AnyRecord), 'match_setup')
  const transitions = objectFromRecord((analysis as unknown as AnyRecord), 'transition_analysis')
  const possessionChains = objectFromRecord((analysis as unknown as AnyRecord), 'possession_chains')
  const homeSetup = objectFromRecord(setup, 'home')
  const awaySetup = objectFromRecord(setup, 'away')
  const confidenceNotes = [
    ...listFromRecord(activeFilter, 'notes'),
    ...listFromRecord(homeSetup, 'confidence_notes'),
    ...listFromRecord(awaySetup, 'confidence_notes'),
    ...listFromRecord(transitions, 'confidence_notes'),
    ...listFromRecord(possessionChains, 'confidence_notes'),
  ].map((item) => s(item)).filter(Boolean)

  return (
    <div className="match-print-report">
      <style>{`
        .match-print-report{display:none}
        @media print{
          @page{size:A4 landscape;margin:9mm}
          html,body{background:#fff!important}
          body *{visibility:hidden!important}
          .match-print-report,.match-print-report *{visibility:visible!important}
          .match-print-report{
            display:block!important;
            position:absolute;
            left:0;
            top:0;
            width:100%;
            padding:0;
            color:#111827;
            background:#fff;
            font-family:Inter,Arial,sans-serif;
            color-scheme:light;
            print-color-adjust:exact;
            -webkit-print-color-adjust:exact;
            --text:#111827;
            --muted:#475569;
            --accent:#0f766e;
            --accent-soft:#ccfbf1;
            --away:#6d28d9;
            --home:#0891b2;
            --border:#d8dee9;
            --panel:#ffffff;
            --surface:#f8fafc;
          }
          .match-print-report h1,.match-print-report h2,.match-print-report h3,.match-print-report h4{color:#111827;margin:0}
          .match-print-report h1{font-size:30px;letter-spacing:-0.03em;line-height:1.05}
          .match-print-report h2{font-size:17px;letter-spacing:-0.015em;line-height:1.15}
          .match-print-report h3{font-size:13px;line-height:1.2}
          .match-print-report h4{font-size:11px;line-height:1.2;text-transform:uppercase;letter-spacing:0.06em;color:#475569}
          .match-print-report p,.match-print-report li{color:#1f2937;font-size:10.5px;line-height:1.45}
          .match-print-report button,.match-print-report select,.match-print-report input{display:none!important}
          .match-print-report .print-cover{
            min-height:179mm;
            display:flex;
            flex-direction:column;
            justify-content:space-between;
            padding:20px 22px;
            border:1px solid #d8dee9;
            border-radius:18px;
            background:linear-gradient(135deg,#ffffff 0%,#f8fafc 55%,#ecfeff 100%);
            break-after:page;
          }
          .match-print-report .print-cover-topline,.match-print-report .print-cover-footer{
            display:flex;
            justify-content:space-between;
            gap:16px;
            color:#475569;
            font-size:10px;
            font-weight:800;
            text-transform:uppercase;
            letter-spacing:0.08em;
          }
          .match-print-report .print-cover-main{
            display:grid;
            grid-template-columns:minmax(0,1fr) 270px;
            gap:26px;
            align-items:center;
          }
          .match-print-report .print-report-label{
            display:inline-flex;
            padding:6px 9px;
            border-radius:999px;
            background:#ccfbf1;
            color:#115e59;
            font-size:10px;
            font-weight:900;
            letter-spacing:0.06em;
            text-transform:uppercase;
            margin-bottom:12px;
          }
          .match-print-report .print-score-card{
            border:1px solid #cbd5e1;
            border-radius:18px;
            padding:18px;
            background:#fff;
            text-align:center;
          }
          .match-print-report .print-score-team{font-size:12px;font-weight:900;color:#334155}
          .match-print-report .print-scoreline{font-size:42px;font-weight:950;letter-spacing:-0.04em;margin:8px 0;color:#0f172a}
          .match-print-report .print-kpi-grid{
            display:grid;
            grid-template-columns:repeat(6,minmax(0,1fr));
            gap:10px;
          }
          .match-print-report .print-kpi-card{
            border:1px solid #d8dee9;
            border-radius:14px;
            padding:10px;
            background:#fff;
            min-width:0;
          }
          .match-print-report .print-kpi-label{
            font-size:9px;
            color:#64748b;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:0.07em;
            margin-bottom:8px;
          }
          .match-print-report .print-kpi-values{display:grid;gap:5px}
          .match-print-report .print-kpi-values div{display:flex;justify-content:space-between;gap:6px;align-items:baseline}
          .match-print-report .print-kpi-values strong{font-size:17px;color:#0f172a}
          .match-print-report .print-kpi-values span{font-size:9px;color:#64748b;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
          .match-print-report .print-report-map{
            display:grid;
            grid-template-columns:repeat(5,minmax(0,1fr));
            gap:8px;
          }
          .match-print-report .print-report-map span{
            border:1px solid #d8dee9;
            border-radius:999px;
            padding:7px 9px;
            color:#334155;
            background:#fff;
            font-size:9px;
            font-weight:900;
            text-align:center;
          }
          .match-print-report section.print-section{
            break-inside:auto;
            border:0;
            border-top:3px solid #0f766e;
            border-radius:0;
            padding:12px 0 0;
            margin:16px 0 0;
            background:#fff;
          }
          .match-print-report section.print-section>h2{
            display:flex;
            align-items:center;
            gap:8px;
            padding:0 0 8px;
            margin:0 0 10px;
            border-bottom:1px solid #d8dee9;
          }
          .match-print-report section.print-section>h2:before{
            content:"";
            width:8px;
            height:8px;
            border-radius:50%;
            background:#0f766e;
            flex:0 0 auto;
          }
          .match-print-report .print-section-body{display:grid;gap:10px}
          .match-print-report .print-side-block{
            break-inside:avoid;
            border:1px solid #d8dee9;
            border-radius:14px;
            padding:11px;
            margin:8px 0;
            background:#fff;
          }
          .match-print-report .print-side-block>h3{
            padding-bottom:8px;
            border-bottom:1px solid #e2e8f0;
            margin-bottom:10px;
          }
          .match-print-report .print-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
          .match-print-report .print-visual-card{
            break-inside:avoid;
            border:1px solid #d8dee9;
            border-radius:14px;
            padding:10px;
            background:#fff;
            min-width:0;
          }
          .match-print-report .print-visual-card>h3{margin-bottom:8px}
          .match-print-report .print-visual-card>div,.match-print-report .print-side-block>div{box-shadow:none!important}
          .match-print-report .print-table{width:100%;border-collapse:collapse;font-size:9.5px;background:#fff}
          .match-print-report .print-table th,.match-print-report .print-table td{border:1px solid #d8dee9;padding:4px 5px;text-align:left;vertical-align:top}
          .match-print-report .print-table th{background:#f1f5f9;color:#111827;font-weight:900}
          .match-print-report svg{max-width:100%;break-inside:avoid}
          .match-print-report svg text{display:block!important;visibility:visible!important}
          .match-print-report [style*="overflow: auto"],.match-print-report [style*="overflow:auto"]{overflow:visible!important;max-height:none!important}
          .match-print-report section[style]{
            box-shadow:none!important;
            border-color:#d8dee9!important;
          }
          .match-print-report table{page-break-inside:auto}
          .match-print-report tr{page-break-inside:avoid;page-break-after:auto}
        }
      `}</style>

      <PrintableReportCover analysis={analysis} />

      <PrintableSection title="Momentum">
        <PrintableMomentumVisual analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Stats summary">
        <SummaryPanel analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Lineups and match setup">
        <MatchSetupPanel analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Team radar">
        <TeamRadarPanel analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Attacking analysis">
        <AttackingTab analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Defensive analysis">
        <PrintableDefensiveFullSection analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Transitions">
        <TransitionsTab analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Set pieces">
        <PrintableSetPiecesFullSection analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Best players">
        <PrintableBestPlayersFullSection analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Recent attacking context">
        <PrintableRecentContextFullSection analysis={analysis} />
      </PrintableSection>

      <PrintableSection title="Audit and data confidence">
        <MatchAuditPanel analysis={analysis} rawColumns={rawColumns} />
        <h3>Data confidence notes</h3>
        <ul>{Array.from(new Set(confidenceNotes)).map((note) => <li key={note}>{note}</li>)}</ul>
      </PrintableSection>
    </div>
  )
}

function TeamRadarPanel({ analysis }: { analysis: MatchAnalysisResponse }) {
  const radar = objectFromRecord((analysis as unknown as AnyRecord), 'team_radar')
  const home = objectFromRecord(radar, 'home')
  const away = objectFromRecord(radar, 'away')
  const homeValues = listFromRecord(home, 'values')
  const awayValues = listFromRecord(away, 'values')
  const metricRows = homeValues.length ? homeValues : listFromRecord(radar, 'metrics')
  const homeTeam = s(home.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayTeam = s(away.team, analysis.selected_fixture?.away_team ?? 'Away')

  if (!metricRows.length || !homeValues.length || !awayValues.length) return null

  const homeById = new Map(homeValues.map((item) => [s(item.id), item]))
  const awayById = new Map(awayValues.map((item) => [s(item.id), item]))
  const rows = metricRows.slice(0, 7).map((item) => {
    const id = s(item.id)
    const homeItem = homeById.get(id) ?? item
    const awayItem = awayById.get(id) ?? item
    return {
      id,
      label: s(item.label, id),
      description: s(item.description),
      homeScore: Math.max(0, Math.min(100, n(homeItem.score))),
      awayScore: Math.max(0, Math.min(100, n(awayItem.score))),
      homeRaw: n(homeItem.raw_value),
      awayRaw: n(awayItem.raw_value),
      homeAgainst: homeItem.raw_against === undefined ? null : n(homeItem.raw_against),
      awayAgainst: awayItem.raw_against === undefined ? null : n(awayItem.raw_against),
    }
  })

  const width = 620
  const height = 390
  const centreX = width / 2
  const centreY = 175
  const radius = 122
  const rings = [25, 50, 75, 100]
  const pointFor = (index: number, score: number) => {
    const angle = (Math.PI * 2 * index) / rows.length - Math.PI / 2
    const distance = (score / 100) * radius
    return {
      x: centreX + Math.cos(angle) * distance,
      y: centreY + Math.sin(angle) * distance,
    }
  }
  const axisPoint = (index: number, pct = 100) => pointFor(index, pct)
  const homePoints = rows.map((row, index) => pointFor(index, row.homeScore)).map((point) => `${point.x},${point.y}`).join(' ')
  const awayPoints = rows.map((row, index) => pointFor(index, row.awayScore)).map((point) => `${point.x},${point.y}`).join(' ')

  return (
    <section style={panelStyle({ padding: 18, marginTop: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <h2 style={titleStyle()}>Team radar</h2>
          <div style={{ ...smallInfoStyle(), marginTop: 5 }}>
            {s(radar.scope) === 'season_to_date'
              ? `Season aggregate from saved ${s(analysis.season)} event rows${n(home.match_count) || n(away.match_count) ? `, ${homeTeam} ${Math.round(n(home.match_count))} matches and ${awayTeam} ${Math.round(n(away.match_count))} matches` : ''}.`
              : 'Selected match radar shown because season event data is not available yet.'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ ...labelStyle(), color: 'rgba(45,216,233,0.96)' }}>{homeTeam}</span>
          <span style={{ ...labelStyle(), color: 'rgba(167,139,250,0.96)' }}>{awayTeam}</span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))', gap: 16, alignItems: 'center', minWidth: 0, width: '100%' }}>
        <div style={{ minWidth: 0, width: '100%', overflow: 'hidden', display: 'flex', justifyContent: 'center' }}>
          <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', maxWidth: 620, height: 'auto', margin: '0 auto', display: 'block' }} role="img" aria-label="Team radar comparison">
            {rings.map((ring) => {
              const points = rows.map((_, index) => axisPoint(index, ring)).map((point) => `${point.x},${point.y}`).join(' ')
              return <polygon key={ring} points={points} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
            })}
            {rows.map((row, index) => {
              const end = axisPoint(index)
              const labelPoint = axisPoint(index, 115)
              return (
                <g key={row.id}>
                  <line x1={centreX} y1={centreY} x2={end.x} y2={end.y} stroke="rgba(255,255,255,0.10)" strokeWidth="1" />
                  <text x={labelPoint.x} y={labelPoint.y} fill="rgba(226,232,240,0.92)" fontSize="12" fontWeight="850" textAnchor={labelPoint.x < centreX - 8 ? 'end' : labelPoint.x > centreX + 8 ? 'start' : 'middle'} dominantBaseline="middle">
                    {row.label}
                  </text>
                </g>
              )
            })}
            <polygon points={homePoints} fill="rgba(45,216,233,0.18)" stroke="rgba(45,216,233,0.96)" strokeWidth="2.5" />
            <polygon points={awayPoints} fill="rgba(167,139,250,0.16)" stroke="rgba(167,139,250,0.96)" strokeWidth="2.5" />
            {rows.map((row, index) => {
              const homePoint = pointFor(index, row.homeScore)
              const awayPoint = pointFor(index, row.awayScore)
              return (
                <g key={`${row.id}-points`}>
                  <circle cx={homePoint.x} cy={homePoint.y} r="4.5" fill="rgba(45,216,233,0.96)" />
                  <circle cx={awayPoint.x} cy={awayPoint.y} r="4.5" fill="rgba(167,139,250,0.96)" />
                </g>
              )
            })}
          </svg>
        </div>

        <div style={{ display: 'grid', gap: 8, minWidth: 0, width: '100%' }}>
          {rows.map((row) => (
            <div key={row.id} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 13, padding: '9px 10px', background: 'rgba(255,255,255,0.035)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '42px 1fr 42px', gap: 8, alignItems: 'center' }}>
                <div style={{ color: 'rgba(45,216,233,0.96)', fontSize: 13, fontWeight: 950, textAlign: 'right' }}>{Math.round(row.homeScore)}</div>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: 12, fontWeight: 950 }}>{row.label}</span>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                      {row.homeAgainst !== null || row.awayAgainst !== null
                        ? `against ${Number(row.homeAgainst ?? 0).toFixed(1)} / ${Number(row.awayAgainst ?? 0).toFixed(1)}`
                        : `raw ${row.homeRaw.toFixed(1)} / ${row.awayRaw.toFixed(1)}`}
                    </span>
                  </div>
                  <div style={{ height: 8, borderRadius: 999, background: 'rgba(255,255,255,0.07)', overflow: 'hidden', marginTop: 6, display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
                    <div style={{ transform: 'scaleX(-1)' }}><div style={{ width: `${row.homeScore}%`, height: '100%', background: 'rgba(45,216,233,0.88)' }} /></div>
                    <div><div style={{ width: `${row.awayScore}%`, height: '100%', background: 'rgba(167,139,250,0.88)' }} /></div>
                  </div>
                </div>
                <div style={{ color: 'rgba(167,139,250,0.96)', fontSize: 13, fontWeight: 950 }}>{Math.round(row.awayScore)}</div>
              </div>
              {row.description && <div style={{ ...smallInfoStyle(), marginTop: 5 }}>{row.description}</div>}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function MetricMini({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 11, padding: 8, background: 'rgba(255,255,255,0.035)' }}>
      <div style={labelStyle()}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 14, fontWeight: 950 }}>{value}</div>
    </div>
  )
}

function styleProfileLabel(profile: unknown): string {
  return cleanVisibleText((profile as AnyRecord | undefined)?.label)
}

function styleProfileEvidence(profile: unknown): string {
  return cleanVisibleText((profile as AnyRecord | undefined)?.evidence)
}

function StyleTagChip({ label, profile, variant = 'match' }: { label: string; profile: unknown; variant?: 'match' | 'season' }) {
  const value = styleProfileLabel(profile)
  if (!value) return null

  const evidence = styleProfileEvidence(profile)
  return (
    <span
      title={evidence || value}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        borderRadius: 999,
        padding: '6px 9px',
        border: variant === 'match' ? '1px solid rgba(45,216,233,0.28)' : '1px solid rgba(255,255,255,0.11)',
        background: variant === 'match' ? 'rgba(45,216,233,0.11)' : 'rgba(255,255,255,0.045)',
        color: 'var(--text)',
        fontSize: 11,
        fontWeight: 850,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ color: 'var(--muted)', fontWeight: 900 }}>{label}</span>
      <span>{value}</span>
    </span>
  )
}

function TeamStyleTagCard({ teamName, tags }: { teamName: string; tags: AnyRecord | undefined }) {
  if (!tags) return null

  const matchTags = (tags.match ?? {}) as AnyRecord
  const seasonTags = (tags.season ?? {}) as AnyRecord
  const shiftNotes = Array.isArray(tags.shift_notes) ? tags.shift_notes.map((item) => cleanVisibleText(item)).filter(Boolean) : []
  const seasonMatchCount = n(seasonTags.match_count)
  const seasonScope = cleanVisibleText(seasonTags.scope)
  const seasonSource = cleanVisibleText(seasonTags.source)
  const showSeasonMeta = seasonScope === 'season_to_date' && seasonMatchCount > 0

  return (
    <div
      style={{
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 14,
        background: 'rgba(255,255,255,0.035)',
        padding: 12,
        minWidth: 280,
        flex: '1 1 360px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13, fontWeight: 950 }}>{teamName}</div>
        {showSeasonMeta && (
          <div style={{ ...labelStyle(), textTransform: 'none', letterSpacing: 0, fontSize: 10 }}>
            Season sample: {seasonMatchCount} matches
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginTop: 10 }}>
        <StyleTagChip label="Match IP" profile={matchTags.in_possession} variant="match" />
        <StyleTagChip label="Season IP" profile={seasonTags.in_possession} variant="season" />
        <StyleTagChip label="Match OOP" profile={matchTags.out_of_possession} variant="match" />
        <StyleTagChip label="Season OOP" profile={seasonTags.out_of_possession} variant="season" />
      </div>

      {shiftNotes.length > 0 && (
        <div style={{ marginTop: 9, fontSize: 11, lineHeight: 1.45, color: 'rgba(250,204,21,0.92)' }}>
          {shiftNotes.join(' ')}
        </div>
      )}

      {seasonSource && seasonScope === 'selected_match_fallback' && (
        <div style={{ marginTop: 9, fontSize: 11, color: 'var(--muted)', lineHeight: 1.45 }}>
          Season tag is using the selected match because a full season event sample was not available.
        </div>
      )}
    </div>
  )
}

function StyleTagsPanel({ analysis, homeName, awayName }: { analysis: MatchAnalysisResponse; homeName: string; awayName: string }) {
  const styleTags = ((analysis as unknown as AnyRecord).style_tags ?? {}) as AnyRecord
  const homeTags = styleTags.home as AnyRecord | undefined
  const awayTags = styleTags.away as AnyRecord | undefined
  const confidenceNotes = Array.isArray(styleTags.confidence_notes) ? styleTags.confidence_notes.map((item) => cleanVisibleText(item)).filter(Boolean) : []

  if (!homeTags && !awayTags) return null

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <TeamStyleTagCard teamName={homeName} tags={homeTags} />
        <TeamStyleTagCard teamName={awayName} tags={awayTags} />
      </div>
      {confidenceNotes.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--muted)', lineHeight: 1.45 }}>
          {confidenceNotes[0]}
        </div>
      )}
    </div>
  )
}


function DashboardSection({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section style={{ marginTop: 18 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <h2 style={titleStyle()}>{title}</h2>
          {note && <div style={{ ...smallInfoStyle(), marginTop: 5 }}>{note}</div>}
        </div>
      </div>
      {children}
    </section>
  )
}

function DashboardGrid({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'stretch', flexWrap: 'wrap' }}>
      {children}
    </div>
  )
}

function DashboardGridMain({ children }: { children: ReactNode }) {
  return <div style={{ flex: '2 1 620px', minWidth: 0 }}>{children}</div>
}

function DashboardGridSide({ children }: { children: ReactNode }) {
  return <div style={{ flex: '1 1 320px', minWidth: 0 }}>{children}</div>
}

function MatchCommandHeader({ analysis, gameState, perspective, rawColumns, reportView, onReportViewChange }: { analysis: MatchAnalysisResponse; gameState: GameStateFilter; perspective: PerspectiveFilter; rawColumns: string[]; reportView: boolean; onReportViewChange: (value: boolean) => void }) {
  const fixture = analysis.selected_fixture
  const activeFilter = objectFromRecord((analysis as unknown as AnyRecord), 'active_filter')
  const selectedState = GAME_STATE_OPTIONS.find((option) => option.value === gameState)?.label ?? 'Full game'
  const homeTeam = s(fixture?.home_team, 'Home')
  const awayTeam = s(fixture?.away_team, 'Away')
  const scoreline = fixture?.home_score === null || fixture?.away_score === null || fixture?.home_score === undefined || fixture?.away_score === undefined
    ? 'Score unavailable'
    : `${fixture.home_score}:${fixture.away_score}`
  const filteredCount = n(activeFilter.event_count_after, analysis.event_count)
  const perspectiveLabel = perspective === 'home' ? `${homeTeam} perspective` : `${awayTeam} perspective`

  return (
    <section style={panelStyle({ padding: 16, marginTop: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ minWidth: 260, flex: '1 1 420px' }}>
          <div style={labelStyle()}>Selected fixture</div>
          <h2 style={{ margin: '5px 0 0', fontSize: 22, fontWeight: 950, letterSpacing: -0.3 }}>{homeTeam} v {awayTeam}</h2>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
            <span style={inlineChipStyle('cyan')}>{homeTeam}</span>
            <span style={inlineChipStyle('slate')}>{scoreline}</span>
            <span style={inlineChipStyle('violet')}>{awayTeam}</span>
          </div>
        </div>

        <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', flex: '1 1 420px', minWidth: 280 }}>
          <div>
            <div style={labelStyle()}>Kick off</div>
            <div style={{ fontSize: 12, fontWeight: 850, marginTop: 5 }}>{formatKickoff(fixture?.kickoff)}</div>
          </div>
          <div>
            <div style={labelStyle()}>Game state</div>
            <div style={{ fontSize: 12, fontWeight: 850, marginTop: 5 }}>{selectedState}</div>
          </div>
          <div>
            <div style={labelStyle()}>Perspective</div>
            <div style={{ fontSize: 12, fontWeight: 850, marginTop: 5 }}>{perspectiveLabel}</div>
          </div>
          <div>
            <div style={labelStyle()}>Events after filter</div>
            <div style={{ fontSize: 12, fontWeight: 850, marginTop: 5 }}>{filteredCount}</div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', flex: '0 1 auto', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <label style={{ ...buttonStyle(reportView), display: 'inline-flex', alignItems: 'center', gap: 8, userSelect: 'none' }}>
            <input
              type="checkbox"
              checked={reportView}
              onChange={(event) => onReportViewChange(event.target.checked)}
              style={{ width: 14, height: 14, accentColor: 'var(--accent)' }}
            />
            Report view
          </label>
          <ExportReportButton analysis={analysis} rawColumns={rawColumns} compact />
        </div>
      </div>
    </section>
  )
}

function PhaseOverviewTab({ homeName, awayName, homePhases, awayPhases }: { homeName: string; awayName: string; homePhases: AnyRecord[]; awayPhases: AnyRecord[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
      <PhaseSummaryList title={`${homeName} overview`} items={homePhases} />
      <PhaseSummaryList title={`${awayName} overview`} items={awayPhases} />
    </div>
  )
}

function PhaseTabs({ analysis }: { analysis: MatchAnalysisResponse }) {
  const [active, setActive] = useState<'overview' | 'attacking' | 'defensive' | 'setpieces' | 'transitions' | 'bestplayers' | 'recentcontext'>('overview')
  const home = analysis.team_summaries?.home as AnyRecord | undefined
  const away = analysis.team_summaries?.away as AnyRecord | undefined
  const homeName = s(home?.team, analysis.selected_fixture?.home_team ?? 'Home')
  const awayName = s(away?.team, analysis.selected_fixture?.away_team ?? 'Away')

  const homePhases = analysis.phase_summaries.home as AnyRecord[]
  const awayPhases = analysis.phase_summaries.away as AnyRecord[]

  return (
    <section style={panelStyle({ padding: 18, marginTop: 16 })}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
        <div>
          <h2 style={titleStyle()}>Phase analysis</h2>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5 }}>
            Tabs separate what they did, how they did it, and what enabled the match pattern.
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[
            ['overview', 'Overview'],
            ['attacking', 'Attacking'],
            ['defensive', 'Defensive'],
            ['setpieces', 'Set pieces'],
            ['transitions', 'Transitions'],
            ['bestplayers', 'Best players'],
            ['recentcontext', 'Recent context'],
          ].map(([key, label]) => (
            <button key={key} type="button" onClick={() => setActive(key as typeof active)} style={buttonStyle(active === key)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <StyleTagsPanel analysis={analysis} homeName={homeName} awayName={awayName} />
      <PhaseVerdicts analysis={analysis} />

      {active === 'overview' && <PhaseOverviewTab homeName={homeName} awayName={awayName} homePhases={homePhases} awayPhases={awayPhases} />}

      {active === 'attacking' && <AttackingTab analysis={analysis} />}

      {active === 'defensive' && <DefensiveTab analysis={analysis} />}

      {active === 'setpieces' && <SetPiecesTab analysis={analysis} />}

      {active === 'transitions' && <TransitionsTab analysis={analysis} />}

      {active === 'bestplayers' && <BestPlayersTab analysis={analysis} />}

      {active === 'recentcontext' && <RecentContextTab analysis={analysis} />}
    </section>
  )
}

export default function MatchAnalysisSection() {
  const [folders, setFolders] = useState<Record<string, string[]>>({})
  const [nation, setNation] = useState('')
  const [tier, setTier] = useState('')
  const [season, setSeason] = useState('')
  const [seasons, setSeasons] = useState<string[]>([])
  const [analysis, setAnalysis] = useState<MatchAnalysisResponse | null>(null)
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null)
  const [selectedTeamFilter, setSelectedTeamFilter] = useState('')
  const [gameState, setGameState] = useState<GameStateFilter>('all')
  const [perspective, setPerspective] = useState<PerspectiveFilter>('home')
  const [reportView, setReportView] = useState(false)
  const [loading, setLoading] = useState(false)
  const [renderStartedAt, setRenderStartedAt] = useState<number | null>(null)
  const [renderStatus, setRenderStatus] = useState<'idle' | 'running' | 'failed'>('idle')
  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState('')
  const [processMessage, setProcessMessage] = useState('')
  const [processProgress, setProcessProgress] = useState<AnyRecord | null>(null)
  const [processLogs, setProcessLogs] = useState<AnyRecord[]>([])
  const [processPopupOpen, setProcessPopupOpen] = useState(false)
  const processStreamRef = useRef<EventSource | null>(null)
  const analysisCacheRef = useRef<Map<string, MatchAnalysisResponse>>(new Map())
  const analysisRequestSeqRef = useRef(0)

  function buildAnalysisCacheKey(matchId: number | null, state: GameStateFilter, viewPerspective: PerspectiveFilter): string {
    return [nation, tier, season, matchId ?? 'none', state, viewPerspective].join('::')
  }

  function applyAnalysisSelectionGuard(data: MatchAnalysisResponse, matchId: number | null) {
    const availableFixtures = selectedTeamFilter
      ? data.fixtures.filter((fixture) => fixture.home_team === selectedTeamFilter || fixture.away_team === selectedTeamFilter)
      : data.fixtures
    const currentStillVisible = matchId
      ? availableFixtures.some((fixture) => fixture.match_id === matchId)
      : false

    if (!currentStillVisible) {
      const firstReady = availableFixtures.find((fixture) => fixture.has_both_events)
      const firstFixture = firstReady ?? availableFixtures[0]
      setSelectedMatchId(firstFixture?.match_id ?? null)
    }
  }

  const nations = useMemo(() => Object.keys(folders), [folders])
  const tierFolders = useMemo(() => (nation ? folders[nation] ?? [] : []), [folders, nation])

  const fixtureTeamOptions = useMemo(() => {
    const names = new Set<string>()
    ;(analysis?.fixtures ?? []).forEach((fixture) => {
      if (fixture.home_team) names.add(fixture.home_team)
      if (fixture.away_team) names.add(fixture.away_team)
    })
    return Array.from(names).sort((a, b) => a.localeCompare(b))
  }, [analysis?.fixtures])

  const filteredFixtures = useMemo(() => {
    const fixtures = analysis?.fixtures ?? []
    if (!selectedTeamFilter) return fixtures
    return fixtures.filter((fixture) => fixture.home_team === selectedTeamFilter || fixture.away_team === selectedTeamFilter)
  }, [analysis?.fixtures, selectedTeamFilter])


  useEffect(() => {
    getScheduleFolders()
      .then((data) => {
        setFolders(data)
        const firstNation = Object.keys(data)[0] ?? ''
        const firstFolder = firstNation ? data[firstNation]?.[0] ?? '' : ''
        setNation(firstNation)
        setTier(firstFolder ? parseTierFromFolder(firstFolder, firstNation) : '')
      })
      .catch((err) => setError(asErrorMessage(err)))

    return () => {
      processStreamRef.current?.close()
    }
  }, [])

  useEffect(() => {
    if (!nation || !tier) return
    getScheduleSeasons(nation, tier)
      .then((data) => {
        setSeasons(data)
        setSeason((current) => current || data[0] || '')
      })
      .catch((err) => setError(asErrorMessage(err)))
  }, [nation, tier])

  useEffect(() => {
    if (!nation || !tier || !season) return

    const requestSeq = analysisRequestSeqRef.current + 1
    analysisRequestSeqRef.current = requestSeq
    const cacheKey = buildAnalysisCacheKey(selectedMatchId, gameState, perspective)
    const cached = analysisCacheRef.current.get(cacheKey)

    if (cached) {
      setAnalysis(cached)
      setError('')
      setRenderStatus('idle')
      setLoading(false)
      applyAnalysisSelectionGuard(cached, selectedMatchId)
      return
    }

    const startedAt = Date.now()
    setRenderStartedAt(startedAt)
    setRenderStatus('running')
    setLoading(true)
    setError('')
    getMatchAnalysis({ nation, tier, season, match_id: selectedMatchId, game_state: gameState, perspective })
      .then((data) => {
        if (requestSeq !== analysisRequestSeqRef.current) return
        const backendDurationMs = data.render_meta?.duration_ms
        const measuredDurationMs = typeof backendDurationMs === 'number' && Number.isFinite(backendDurationMs)
          ? backendDurationMs
          : Date.now() - startedAt
        rememberAnalysisRenderDuration('match_analysis', measuredDurationMs)
        setRenderStatus('idle')
        analysisCacheRef.current.set(cacheKey, data)
        setAnalysis(data)
        applyAnalysisSelectionGuard(data, selectedMatchId)
      })
      .catch((err) => {
        if (requestSeq !== analysisRequestSeqRef.current) return
        setRenderStatus('failed')
        setError(asErrorMessage(err))
      })
      .finally(() => {
        if (requestSeq !== analysisRequestSeqRef.current) return
        setLoading(false)
      })
  }, [nation, tier, season, selectedMatchId, selectedTeamFilter, gameState, perspective])

  const rawColumns = useMemo(() => {
    if (!analysis?.raw_events?.length) return []
    return Object.keys(analysis.raw_events[0])
  }, [analysis])

  function handleStopProcessedStore() {
    processStreamRef.current?.close()
    processStreamRef.current = null
    setProcessing(false)
    setProcessMessage('Stopped listening to the Parquet rebuild stream. The backend may continue until the current request finishes.')
  }

  function handleRebuildProcessedStore() {
    if (!nation || !tier || !season || processing) return

    processStreamRef.current?.close()
    setProcessing(true)
    setProcessPopupOpen(true)
    setProcessProgress(null)
    setProcessLogs([])
    setProcessMessage('Preparing processed Parquet rebuild...')
    setError('')

    let completedCleanly = false

    processStreamRef.current = openProcessedStoreStream({
      nation,
      tier,
      season,
      force: true,
      onEvent: (event) => {
        const row: AnyRecord = {
          ...(event as AnyRecord),
          time: new Date().toLocaleTimeString(),
        }
        const kind = s(event.kind)
        setProcessProgress(event as AnyRecord)
        setProcessMessage(s(event.message, 'Building processed Parquet store...'))
        setProcessLogs((prev) => [row, ...prev].slice(0, 10))

        if (kind === 'complete') {
          completedCleanly = true
        }
        if (kind === 'error') {
          setError(s(event.message, 'Processed store rebuild failed.'))
        }
      },
      onError: (message) => {
        setError(message)
        setProcessing(false)
      },
      onDone: async () => {
        processStreamRef.current = null
        setProcessing(false)
        if (!completedCleanly) return
        try {
          analysisCacheRef.current.clear()
          const rebuildRefreshSeq = analysisRequestSeqRef.current + 1
          analysisRequestSeqRef.current = rebuildRefreshSeq
          const refreshed = await getMatchAnalysis({ nation, tier, season, match_id: selectedMatchId, game_state: gameState, perspective })
          if (rebuildRefreshSeq !== analysisRequestSeqRef.current) return
          analysisCacheRef.current.set(buildAnalysisCacheKey(selectedMatchId, gameState, perspective), refreshed)
          setAnalysis(refreshed)
        } catch (err) {
          setError(asErrorMessage(err))
        }
      },
    })
  }

  return (
    <div>
      <ProcessedStoreProgressPopup
        visible={processPopupOpen || processing}
        running={processing}
        event={processProgress}
        logs={processLogs}
        message={processMessage}
        onClose={() => setProcessPopupOpen(false)}
        onStop={handleStopProcessedStore}
      />

      {(!reportView || !analysis?.selected_fixture) && (
        <section className="card" style={{ marginBottom: 14, padding: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 520px', minWidth: 260 }}>
            <h2 style={{ margin: 0, fontSize: 19 }}>Match selection</h2>
            <p style={{ margin: '5px 0 0', color: 'var(--muted)', fontSize: 12, lineHeight: 1.4 }}>
              Select a saved fixture and rebuild the processed store when the source event files change.
            </p>
          </div>
          <button type="button" onClick={handleRebuildProcessedStore} disabled={!nation || !tier || !season || processing} style={{ ...buttonStyle(true), padding: '9px 11px' }}>
            {processing ? 'Building store...' : 'Rebuild processed store'}
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10, marginTop: 13 }}>
          <label>
            <span style={labelStyle()}>Nation</span>
            <select
              value={nation}
              onChange={(event) => {
                const nextNation = event.target.value
                const nextFolder = folders[nextNation]?.[0] ?? ''
                setNation(nextNation)
                setTier(nextFolder ? parseTierFromFolder(nextFolder, nextNation) : '')
                setSeason('')
                setSelectedMatchId(null)
                setSelectedTeamFilter('')
                setGameState('all')
                setPerspective('home')
                setAnalysis(null)
              }}
              style={{ ...FIELD_STYLE, padding: '9px 10px' }}
            >
              {nations.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>

          <label>
            <span style={labelStyle()}>Tier</span>
            <select
              value={tier}
              onChange={(event) => {
                setTier(event.target.value)
                setSeason('')
                setSelectedMatchId(null)
                setSelectedTeamFilter('')
                setGameState('all')
                setPerspective('home')
                setAnalysis(null)
              }}
              style={{ ...FIELD_STYLE, padding: '9px 10px' }}
            >
              {tierFolders.map((folder) => {
                const parsedTier = parseTierFromFolder(folder, nation)
                return <option key={folder} value={parsedTier}>{parsedTier}</option>
              })}
            </select>
          </label>

          <label>
            <span style={labelStyle()}>Season</span>
            <select
              value={season}
              onChange={(event) => {
                setSeason(event.target.value)
                setSelectedMatchId(null)
                setSelectedTeamFilter('')
                setGameState('all')
                setPerspective('home')
                setAnalysis(null)
              }}
              style={{ ...FIELD_STYLE, padding: '9px 10px' }}
            >
              {seasons.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>

          <label>
            <span style={labelStyle()}>Team filter</span>
            <select
              value={selectedTeamFilter}
              onChange={(event) => {
                setSelectedTeamFilter(event.target.value)
                setSelectedMatchId(null)
                setGameState('all')
                setPerspective('home')
              }}
              style={{ ...FIELD_STYLE, padding: '9px 10px' }}
            >
              <option value="">All teams</option>
              {fixtureTeamOptions.map((team) => <option key={team} value={team}>{team}</option>)}
            </select>
          </label>

          <label style={{ gridColumn: '1 / -1' }}>
            <span style={labelStyle()}>Fixture</span>
            <select
              value={selectedMatchId ?? ''}
              onChange={(event) => {
                setSelectedMatchId(event.target.value ? Number(event.target.value) : null)
                setGameState('all')
                setPerspective('home')
              }}
              style={{ ...FIELD_STYLE, padding: '9px 10px' }}
            >
              <option value="">Choose a match</option>
              {selectedTeamFilter && filteredFixtures.length === 0 && <option value="" disabled>No matches for selected team</option>}
              {filteredFixtures.map((fixture) => (
                <option key={fixture.match_id} value={fixture.match_id}>
                  {fixture.has_both_events ? 'Ready' : 'Missing events'} • {fixtureLabel(fixture)}
                </option>
              ))}
            </select>
          </label>
        </div>

        {(processMessage || error) && (
          <div style={{ marginTop: 10, fontSize: 12, color: error ? '#fca5a5' : 'var(--muted)' }}>
            {error || processMessage}
          </div>
        )}
        </section>
      )}

      {(loading || renderStatus === 'failed') && (
        <AnalysisRenderProgress
          kind="match_analysis"
          status={renderStatus === 'failed' ? 'failed' : 'running'}
          startedAt={renderStartedAt}
          message={error}
        />
      )}

      {analysis && analysis.selected_fixture && !loading && (
        <>
          <MatchCommandHeader analysis={analysis} gameState={gameState} perspective={perspective} rawColumns={rawColumns} reportView={reportView} onReportViewChange={setReportView} />
          {!reportView && (
            <GameStateFilterPanel
              analysis={analysis}
              gameState={gameState}
              perspective={perspective}
              onGameStateChange={setGameState}
              onPerspectiveChange={setPerspective}
            />
          )}
        </>
      )}

      {analysis && analysis.selected_fixture && analysis.event_count > 0 && !loading && (
        <>
          <DashboardSection title="Executive overview" note="Immediate read on the scoreline, match stats, team profile comparison and match flow.">
            <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
              <SummaryPanel analysis={analysis} />
              <TeamRadarPanel analysis={analysis} />
              <MomentumChart analysis={analysis} />
            </div>
          </DashboardSection>

          <DashboardSection title="Match context" note="Lineups and setup explain the phase interpretation that follows.">
            <MatchSetupPanel analysis={analysis} />
          </DashboardSection>

          <PlayerInfluenceSummary analysis={analysis} />
          <PhaseTabs analysis={analysis} />
          <VideoChecksRequired />
          <MatchAuditPanel analysis={analysis} rawColumns={rawColumns} reportView={reportView} />
        </>
      )}

      {analysis && selectedMatchId && analysis.event_count === 0 && !loading && (
        <div className="card" style={{ color: 'var(--muted)' }}>
          {n(objectFromRecord((analysis as unknown as AnyRecord), 'active_filter').event_count_before) > 0
            ? 'No events match the selected game state filter.'
            : 'This fixture exists in the saved schedule, but there is no combined saved match frame to render yet.'}
        </div>
      )}
    </div>
  )
}