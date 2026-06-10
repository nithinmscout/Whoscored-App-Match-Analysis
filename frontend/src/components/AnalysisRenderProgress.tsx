import { useEffect, useMemo, useState, type CSSProperties } from 'react'

export type AnalysisRenderKind = 'team_analysis' | 'match_analysis' | 'league_analysis'
export type AnalysisRenderStatus = 'running' | 'failed' | 'complete'

type RenderPhase = {
  label: string
  weight: number
}

type RenderConfig = {
  title: string
  storageKey: string
  fallbackSeconds: number
  phases: RenderPhase[]
}

type StoredEstimate = {
  estimateSeconds: number
  samples: number
  lastDurationSeconds: number
  updatedAt: number
}

type EstimateSnapshot = {
  seconds: number
  source: 'history' | 'fallback'
  samples: number
  lastDurationSeconds: number | null
}

type RuntimeEstimate = {
  estimatedTotalSeconds: number
  remainingSeconds: number
  progressPercent: number
  isExtended: boolean
}

type AnalysisRenderProgressProps = {
  kind?: AnalysisRenderKind | string | undefined
  status: AnalysisRenderStatus
  startedAt: number | null
  message?: string | undefined
  style?: CSSProperties | undefined
}

const MIN_ESTIMATE_SECONDS = 2
const MAX_ESTIMATE_SECONDS = 600
const ESTIMATE_TTL_MS = 1000 * 60 * 60 * 24 * 30

const DEFAULT_RENDER_KIND: AnalysisRenderKind = 'team_analysis'

const RENDER_CONFIG: Record<AnalysisRenderKind, RenderConfig> = {
  league_analysis: {
    title: 'League analysis dashboard is building',
    storageKey: 'who_render_eta_league_analysis',
    fallbackSeconds: 20,
    phases: [
      { label: 'Preparing league analysis workspace', weight: 8 },
      { label: 'Loading saved season event files', weight: 22 },
      { label: 'Building team style profiles', weight: 19 },
      { label: 'Scoring xG and xT context', weight: 20 },
      { label: 'Running correlation analysis', weight: 11 },
      { label: 'Running PCA and clustering', weight: 12 },
      { label: 'Preparing league style dashboard', weight: 8 },
    ],
  },
  team_analysis: {
    title: 'Team analysis dashboard is building',
    storageKey: 'who_render_eta_team_analysis',
    fallbackSeconds: 18,
    phases: [
      { label: 'Preparing team profiling workspace', weight: 6 },
      { label: 'Loading saved schedule', weight: 7 },
      { label: 'Reading selected team files', weight: 17 },
      { label: 'Resolving saved opponent context', weight: 10 },
      { label: 'Building club profile overview', weight: 11 },
      { label: 'Building attacking profile', weight: 11 },
      { label: 'Building defensive profile', weight: 11 },
      { label: 'Building transitions and set pieces', weight: 10 },
      { label: 'Building player contribution table', weight: 8 },
      { label: 'Building season comparison', weight: 5 },
      { label: 'Rendering profiling dashboard', weight: 4 },
    ],
  },
  match_analysis: {
    title: 'Match analysis dashboard is building',
    storageKey: 'who_render_eta_match_analysis',
    fallbackSeconds: 22,
    phases: [
      { label: 'Preparing match workspace', weight: 6 },
      { label: 'Loading saved schedule', weight: 7 },
      { label: 'Resolving selected fixture', weight: 7 },
      { label: 'Reading saved event files', weight: 14 },
      { label: 'Normalising event data', weight: 10 },
      { label: 'Building team summaries', weight: 9 },
      { label: 'Calculating attacking and defensive views', weight: 14 },
      { label: 'Building momentum and territory views', weight: 10 },
      { label: 'Building set piece and transition views', weight: 9 },
      { label: 'Preparing best players and raw events', weight: 8 },
      { label: 'Rendering dashboard', weight: 6 },
    ],
  },
}


function isAnalysisRenderKind(value: unknown): value is AnalysisRenderKind {
  return value === 'team_analysis' || value === 'match_analysis' || value === 'league_analysis'
}

function normaliseRenderKind(kind: unknown): AnalysisRenderKind {
  return isAnalysisRenderKind(kind) ? kind : DEFAULT_RENDER_KIND
}

function getRenderConfig(kind: unknown): RenderConfig {
  return RENDER_CONFIG[normaliseRenderKind(kind)]
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

function isUsableSeconds(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= MIN_ESTIMATE_SECONDS && value <= MAX_ESTIMATE_SECONDS
}

function normaliseEstimateSeconds(value: number): number {
  return Math.round(clamp(value, MIN_ESTIMATE_SECONDS, MAX_ESTIMATE_SECONDS) * 10) / 10
}

function removeBadStoredEstimate(storageKey: string): void {
  try {
    window.localStorage.removeItem(storageKey)
  } catch {
    return
  }
}

function parseStoredEstimate(storageKey: string): EstimateSnapshot | null {
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return null

    const legacySeconds = Number(raw)
    if (Number.isFinite(legacySeconds)) {
      if (!isUsableSeconds(legacySeconds)) {
        removeBadStoredEstimate(storageKey)
        return null
      }

      return {
        seconds: normaliseEstimateSeconds(legacySeconds),
        source: 'history',
        samples: 1,
        lastDurationSeconds: normaliseEstimateSeconds(legacySeconds),
      }
    }

    const parsed = JSON.parse(raw) as Partial<StoredEstimate>
    const estimateSeconds = Number(parsed.estimateSeconds)
    const samples = Number(parsed.samples)
    const lastDurationSeconds = Number(parsed.lastDurationSeconds)
    const updatedAt = Number(parsed.updatedAt)

    const isFresh = Number.isFinite(updatedAt) && Date.now() - updatedAt <= ESTIMATE_TTL_MS
    if (!isFresh || !isUsableSeconds(estimateSeconds)) {
      removeBadStoredEstimate(storageKey)
      return null
    }

    return {
      seconds: normaliseEstimateSeconds(estimateSeconds),
      source: 'history',
      samples: Number.isFinite(samples) ? Math.max(1, Math.round(samples)) : 1,
      lastDurationSeconds: isUsableSeconds(lastDurationSeconds) ? normaliseEstimateSeconds(lastDurationSeconds) : null,
    }
  } catch {
    removeBadStoredEstimate(storageKey)
    return null
  }
}

function getEstimateSnapshot(kind: unknown): EstimateSnapshot {
  const config = getRenderConfig(kind)
  const stored = parseStoredEstimate(config.storageKey)
  if (stored) return stored

  return {
    seconds: config.fallbackSeconds,
    source: 'fallback',
    samples: 0,
    lastDurationSeconds: null,
  }
}

export function getAnalysisRenderEstimateSeconds(kind: AnalysisRenderKind | string | undefined): number {
  return getEstimateSnapshot(kind).seconds
}

export function rememberAnalysisRenderDuration(kind: AnalysisRenderKind | string | undefined, durationMs: number): void {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return

  const config = getRenderConfig(kind)
  const actualSeconds = normaliseEstimateSeconds(durationMs / 1000)
  const previous = parseStoredEstimate(config.storageKey)

  let nextEstimate = actualSeconds
  let nextSamples = 1

  if (previous) {
    const existing = previous.seconds
    nextSamples = Math.min(previous.samples + 1, 30)

    if (actualSeconds > existing * 1.6) {
      nextEstimate = (existing * 0.35) + (actualSeconds * 0.65)
    } else if (actualSeconds < existing * 0.55) {
      nextEstimate = (existing * 0.75) + (actualSeconds * 0.25)
    } else {
      nextEstimate = (existing * 0.6) + (actualSeconds * 0.4)
    }
  }

  const payload: StoredEstimate = {
    estimateSeconds: normaliseEstimateSeconds(nextEstimate),
    samples: nextSamples,
    lastDurationSeconds: actualSeconds,
    updatedAt: Date.now(),
  }

  try {
    window.localStorage.setItem(config.storageKey, JSON.stringify(payload))
  } catch {
    return
  }
}

function formatElapsed(seconds: number): string {
  if (seconds < 1) return '0s'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`
}

function runtimeEstimate(status: AnalysisRenderStatus, elapsedSeconds: number, estimateSeconds: number): RuntimeEstimate {
  if (status === 'complete') {
    return {
      estimatedTotalSeconds: Math.max(elapsedSeconds, estimateSeconds),
      remainingSeconds: 0,
      progressPercent: 100,
      isExtended: false,
    }
  }

  const safeEstimate = Math.max(estimateSeconds, MIN_ESTIMATE_SECONDS)
  const hasOverrun = elapsedSeconds > safeEstimate * 0.92
  const estimatedTotalSeconds = hasOverrun
    ? Math.min(MAX_ESTIMATE_SECONDS, Math.max(safeEstimate, elapsedSeconds / 0.88))
    : safeEstimate

  const rawProgress = (elapsedSeconds / Math.max(estimatedTotalSeconds, 1)) * 100
  const cap = status === 'failed' ? 96 : 95
  const progressPercent = clamp(rawProgress, 4, cap)

  return {
    estimatedTotalSeconds,
    remainingSeconds: Math.max(0, estimatedTotalSeconds - elapsedSeconds),
    progressPercent,
    isExtended: hasOverrun,
  }
}

function formatRemaining(
  status: AnalysisRenderStatus,
  elapsedSeconds: number,
  snapshot: EstimateSnapshot,
  runtime: RuntimeEstimate,
): string {
  if (status === 'failed') return 'Stopped before the dashboard completed'
  if (status === 'complete') return 'Dashboard ready'

  if (snapshot.source === 'fallback' && elapsedSeconds < 2) {
    return 'Calibrating estimate'
  }

  if (runtime.remainingSeconds <= 2) return 'Almost done'
  if (runtime.remainingSeconds <= 5) return 'Less than 5s left'
  if (runtime.remainingSeconds <= 20) return `About ${Math.round(runtime.remainingSeconds)}s left`

  const rounded = Math.max(5, Math.round(runtime.remainingSeconds / 5) * 5)
  return `About ${rounded}s left`
}

function progressDetail(snapshot: EstimateSnapshot, runtime: RuntimeEstimate): string {
  if (snapshot.source === 'history') {
    const sampleText = snapshot.samples === 1 ? 'one completed render' : `${snapshot.samples} completed renders`
    const lastText = snapshot.lastDurationSeconds !== null ? ` Last run ${formatElapsed(snapshot.lastDurationSeconds)}.` : ''
    const overrunText = runtime.isExtended ? ' Adjusting live because this run is taking longer than the stored estimate.' : ''
    return `ETA is based on ${sampleText}.${lastText}${overrunText}`
  }

  return 'ETA is using the starter estimate until this dashboard completes once. It will learn from the backend duration after completion.'
}

function phaseStates(phases: RenderPhase[], progressPercent: number) {
  const totalWeight = phases.reduce((sum, phase) => sum + phase.weight, 0) || 1
  let cursor = 0

  return phases.map((phase) => {
    const start = (cursor / totalWeight) * 100
    cursor += phase.weight
    const end = (cursor / totalWeight) * 100
    const state = progressPercent >= end ? 'completed' : progressPercent >= start ? 'active' : 'pending'
    return { ...phase, state }
  })
}

function stateLabel(state: string, failed: boolean): string {
  if (state === 'completed') return 'Done'
  if (state === 'active') return failed ? 'Needs retry' : 'Now'
  return 'Pending'
}

function stateStyle(state: string, failed: boolean): CSSProperties {
  if (state === 'completed') {
    return {
      borderColor: 'rgba(52,211,153,0.35)',
      background: 'rgba(52,211,153,0.10)',
      color: '#bbf7d0',
    }
  }
  if (state === 'active') {
    return {
      borderColor: failed ? 'rgba(248,113,113,0.40)' : 'rgba(45,216,233,0.45)',
      background: failed ? 'rgba(248,113,113,0.12)' : 'rgba(45,216,233,0.14)',
      color: failed ? '#fecaca' : 'var(--text)',
    }
  }
  return {
    borderColor: 'rgba(255,255,255,0.08)',
    background: 'rgba(255,255,255,0.035)',
    color: 'var(--muted)',
  }
}

export default function AnalysisRenderProgress({ kind, status, startedAt, message, style }: AnalysisRenderProgressProps) {
  const safeKind = normaliseRenderKind(kind)
  const config = getRenderConfig(safeKind)
  const [now, setNow] = useState(() => Date.now())
  const safeStartedAt = startedAt ?? now
  const failed = status === 'failed'

  useEffect(() => {
    if (status !== 'running') return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [status])

  useEffect(() => {
    setNow(Date.now())
  }, [safeKind, safeStartedAt, status])

  const snapshot = useMemo(() => getEstimateSnapshot(safeKind), [safeKind, safeStartedAt])
  const elapsedSeconds = Math.max(0, (now - safeStartedAt) / 1000)
  const runtime = runtimeEstimate(status, elapsedSeconds, snapshot.seconds)
  const phases = phaseStates(config.phases, runtime.progressPercent)
  const activePhase = phases.find((phase) => phase.state === 'active') ?? phases[phases.length - 1]
  const remainingLabel = formatRemaining(status, elapsedSeconds, snapshot, runtime)

  return (
    <section
      className="card"
      style={{
        padding: 16,
        marginBottom: 16,
        border: failed ? '1px solid rgba(248,113,113,0.28)' : '1px solid rgba(45,216,233,0.20)',
        background: failed
          ? 'linear-gradient(135deg, rgba(127,29,29,0.24), rgba(17,24,39,0.94))'
          : 'linear-gradient(135deg, rgba(45,216,233,0.12), rgba(17,24,39,0.96))',
        ...style,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <div style={{ minWidth: 240, flex: '1 1 420px' }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.8, fontWeight: 850 }}>
            {failed ? 'Render failed' : 'Render progress'}
          </div>
          <h3 style={{ margin: '6px 0 0', fontSize: 18, fontWeight: 950 }}>{config.title}</h3>
          <div style={{ marginTop: 7, fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
            {failed ? (message || 'The request did not complete. Check the normal error message below.') : progressDetail(snapshot, runtime)}
          </div>
          {!failed && message && (
            <div style={{ marginTop: 7, fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
              {message}
            </div>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(95px, 1fr))', gap: 8, flex: '0 1 390px', minWidth: 280 }}>
          <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.7, fontWeight: 850 }}>Elapsed</div>
            <div style={{ fontSize: 16, fontWeight: 950, marginTop: 4 }}>{formatElapsed(elapsedSeconds)}</div>
          </div>
          <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.7, fontWeight: 850 }}>ETA</div>
            <div style={{ fontSize: 16, fontWeight: 950, marginTop: 4 }}>{remainingLabel}</div>
          </div>
          <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: 0.7, fontWeight: 850 }}>Progress</div>
            <div style={{ fontSize: 16, fontWeight: 950, marginTop: 4 }}>{Math.round(runtime.progressPercent)}%</div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontSize: 12, fontWeight: 900 }}>{activePhase?.label ?? 'Preparing dashboard'}</div>
          <div style={{ fontSize: 11, color: failed ? '#fecaca' : 'var(--muted)' }}>
            {failed ? 'Request stopped' : 'Request still running'}
          </div>
        </div>
        <div style={{ height: 9, borderRadius: 999, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
          <div
            style={{
              width: `${runtime.progressPercent}%`,
              height: '100%',
              borderRadius: 999,
              background: failed
                ? 'linear-gradient(90deg, rgba(248,113,113,0.92), rgba(251,146,60,0.88))'
                : 'linear-gradient(90deg, rgba(45,216,233,0.92), rgba(167,139,250,0.88))',
              transition: 'width 420ms ease',
            }}
          />
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 8, marginTop: 14 }}>
        {phases.map((phase) => (
          <div key={phase.label} style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center', border: '1px solid', borderRadius: 13, padding: '9px 10px', fontSize: 12, ...stateStyle(phase.state, failed) }}>
            <span style={{ fontWeight: phase.state === 'active' ? 900 : 750 }}>{phase.label}</span>
            <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6, fontWeight: 900 }}>{stateLabel(phase.state, failed)}</span>
          </div>
        ))}
      </div>
    </section>
  )
}