import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { openFetchEventsStream } from './api'
import type { TableRow } from '../types/api'

interface AppConfigValue {
  appName: string
}

type CellValue = string | number | boolean | null

type StreamEvent = {
  kind?: string
  message?: string
  reason?: string
  [key: string]: unknown
}

export type EventStreamRow = TableRow & {
  time: string
  kind: string
  message: string
}

export type EventFetchStartParams = {
  league: string
  season: string
  nation: string
  tier: string
  headless: boolean
  browserpath?: string
  only_finished: boolean
  overwrite: boolean
  retry_failed: boolean
  fail_fast: boolean
  scrape_positions: boolean
}

interface EventFetchProgressValue {
  isRunning: boolean
  rows: EventStreamRow[]
  completed: number
  total: number
  progressPct: number
  currentMatchId: number | null
  currentMessage: string
  currentKind: string
  rowsWritten: number | null
  failedCount: number | null
  browserPath: string
  lastError: string
  league: string
  season: string
  nation: string
  tier: string
  start: (params: EventFetchStartParams) => boolean
  stop: () => void
  clear: () => void
}

interface EventFetchState {
  isRunning: boolean
  rows: EventStreamRow[]
  completed: number
  total: number
  currentMatchId: number | null
  currentMessage: string
  currentKind: string
  rowsWritten: number | null
  failedCount: number | null
  browserPath: string
  lastError: string
  league: string
  season: string
  nation: string
  tier: string
  startedAt: number | null
  finishedAt: number | null
}

const initialEventFetchState: EventFetchState = {
  isRunning: false,
  rows: [],
  completed: 0,
  total: 0,
  currentMatchId: null,
  currentMessage: 'No event fetch has started yet.',
  currentKind: 'idle',
  rowsWritten: null,
  failedCount: null,
  browserPath: '',
  lastError: '',
  league: '',
  season: '',
  nation: '',
  tier: '',
  startedAt: null,
  finishedAt: null,
}

const AppConfigContext = createContext<AppConfigValue>({
  appName: 'WhoScored Match Analysis',
})

const EventFetchProgressContext = createContext<EventFetchProgressValue | null>(null)

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function scalarNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function tableCell(value: unknown): CellValue {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
  return JSON.stringify(value)
}

function eventToRow(event: StreamEvent): EventStreamRow {
  const safeCells = Object.fromEntries(
    Object.entries(event).map(([key, value]) => [key, tableCell(value)]),
  ) as TableRow

  return {
    ...safeCells,
    time: new Date().toLocaleTimeString(),
    kind: scalarText(event.kind) || 'event',
    message: scalarText(event.message) || scalarText(event.reason),
  }
}

function clampProgress(completed: number, total: number): number {
  if (!Number.isFinite(total) || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((completed / total) * 100)))
}

function ProgressStat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        border: '1px solid rgba(255,255,255,0.09)',
        borderRadius: 12,
        padding: '8px 10px',
        background: 'rgba(255,255,255,0.035)',
        minWidth: 0,
      }}
    >
      <div style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 850, textTransform: 'uppercase', letterSpacing: 0.7 }}>{label}</div>
      <div style={{ marginTop: 4, color: 'var(--text)', fontSize: 13, fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {value}
      </div>
    </div>
  )
}

function FloatingEventFetchProgress({
  state,
  progressPct,
  stop,
  clear,
}: {
  state: EventFetchState
  progressPct: number
  stop: () => void
  clear: () => void
}) {
  const [minimised, setMinimised] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    if (state.isRunning) {
      setDismissed(false)
      setMinimised(false)
    }
  }, [state.isRunning])

  if (dismissed || (!state.isRunning && !state.rows.length)) return null

  const matchLabel = state.total > 0
    ? `${Math.max(state.completed, 0)} of ${state.total}`
    : state.currentMatchId ? String(state.currentMatchId) : 'Not started'

  const folderLabel = [state.nation, state.tier, state.season].filter(Boolean).join(' ')
  const statusColour = state.isRunning
    ? 'var(--accent)'
    : state.lastError
      ? '#fca5a5'
      : '#86efac'

  return (
    <div
      style={{
        position: 'fixed',
        top: 18,
        right: 18,
        zIndex: 80,
        width: minimised ? 270 : 390,
        maxWidth: 'calc(100vw - 36px)',
        border: '1px solid rgba(255,255,255,0.13)',
        borderRadius: 22,
        background: 'linear-gradient(145deg, rgba(9,14,26,0.96), rgba(16,23,42,0.94))',
        boxShadow: '0 24px 60px rgba(0,0,0,0.36)',
        backdropFilter: 'blur(18px)',
        overflow: 'hidden',
      }}
    >
      <div style={{ padding: minimised ? 12 : 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: statusColour, fontSize: 11, fontWeight: 950, letterSpacing: 1, textTransform: 'uppercase' }}>
              {state.isRunning ? 'Event fetch running' : state.lastError ? 'Event fetch needs checking' : 'Event fetch finished'}
            </div>
            <div style={{ marginTop: 5, color: 'var(--text)', fontSize: 14, fontWeight: 950, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {state.league || 'WhoScored event stream'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              type="button"
              onClick={() => setMinimised((value) => !value)}
              style={{
                border: '1px solid rgba(255,255,255,0.12)',
                background: 'rgba(255,255,255,0.06)',
                color: 'var(--text)',
                borderRadius: 10,
                padding: '6px 8px',
                cursor: 'pointer',
                fontWeight: 900,
              }}
            >
              {minimised ? 'Open' : 'Hide'}
            </button>
            {!state.isRunning && (
              <button
                type="button"
                onClick={() => {
                  clear()
                  setDismissed(true)
                }}
                style={{
                  border: '1px solid rgba(255,255,255,0.12)',
                  background: 'rgba(255,255,255,0.06)',
                  color: 'var(--text)',
                  borderRadius: 10,
                  padding: '6px 8px',
                  cursor: 'pointer',
                  fontWeight: 900,
                }}
              >
                Clear
              </button>
            )}
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 7, color: 'var(--muted)', fontSize: 11, fontWeight: 850 }}>
            <span>{matchLabel}</span>
            <span>{progressPct}%</span>
          </div>
          <div style={{ height: 9, borderRadius: 999, background: 'rgba(255,255,255,0.08)', overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)' }}>
            <div
              style={{
                width: `${progressPct}%`,
                height: '100%',
                borderRadius: 999,
                background: 'linear-gradient(90deg, rgba(45,216,233,0.95), rgba(134,59,255,0.92))',
                transition: 'width 220ms ease',
              }}
            />
          </div>
        </div>

        {!minimised && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 12 }}>
              <ProgressStat label="Current match" value={state.currentMatchId ? String(state.currentMatchId) : 'Pending'} />
              <ProgressStat label="Rows saved" value={state.rowsWritten === null ? 'Pending' : String(state.rowsWritten)} />
              <ProgressStat label="Failures" value={state.failedCount === null ? '0' : String(state.failedCount)} />
            </div>

            <div style={{ marginTop: 12, border: '1px solid rgba(255,255,255,0.09)', borderRadius: 14, padding: 10, background: 'rgba(255,255,255,0.03)' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 850, textTransform: 'uppercase', letterSpacing: 0.7 }}>Latest detail</div>
              <div style={{ color: 'var(--text)', fontSize: 12, lineHeight: 1.45, marginTop: 5 }}>{state.currentMessage}</div>
              {folderLabel && <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 7 }}>{folderLabel}</div>}
              {state.browserPath && <div style={{ color: 'var(--muted)', fontSize: 10, marginTop: 5, wordBreak: 'break-all' }}>{state.browserPath}</div>}
              {state.lastError && <div style={{ color: '#fca5a5', fontSize: 11, marginTop: 7 }}>{state.lastError}</div>}
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginTop: 12 }}>
              <div style={{ color: 'var(--muted)', fontSize: 11 }}>
                {state.startedAt ? `Started ${new Date(state.startedAt).toLocaleTimeString()}` : 'Not started'}
              </div>
              {state.isRunning && (
                <button
                  type="button"
                  onClick={stop}
                  style={{
                    border: '1px solid rgba(248,113,113,0.35)',
                    background: 'rgba(248,113,113,0.14)',
                    color: '#fecaca',
                    borderRadius: 11,
                    padding: '8px 10px',
                    cursor: 'pointer',
                    fontWeight: 900,
                    fontSize: 12,
                  }}
                >
                  Stop stream
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export function AppProvider({ children }: { children: ReactNode }) {
  const streamRef = useRef<EventSource | null>(null)
  const stateRef = useRef<EventFetchState>(initialEventFetchState)
  const [eventState, setEventState] = useState<EventFetchState>(initialEventFetchState)

  useEffect(() => {
    stateRef.current = eventState
  }, [eventState])

  useEffect(() => {
    return () => {
      streamRef.current?.close()
    }
  }, [])

  const stop = useCallback(() => {
    streamRef.current?.close()
    streamRef.current = null
    setEventState((prev) => ({
      ...prev,
      isRunning: false,
      currentKind: 'stopped',
      currentMessage: 'Event stream stopped locally.',
      finishedAt: Date.now(),
    }))
  }, [])

  const clear = useCallback(() => {
    setEventState((prev) => {
      if (prev.isRunning) {
        return { ...prev, rows: [] }
      }
      return initialEventFetchState
    })
  }, [])

  const start = useCallback((params: EventFetchStartParams) => {
    if (stateRef.current.isRunning) return false

    streamRef.current?.close()
    setEventState({
      ...initialEventFetchState,
      isRunning: true,
      league: params.league,
      season: params.season,
      nation: params.nation,
      tier: params.tier,
      currentMessage: 'Starting event stream.',
      currentKind: 'status',
      startedAt: Date.now(),
    })

    streamRef.current = openFetchEventsStream({
      ...params,
      onEvent: (event) => {
        const eventRecord = event as StreamEvent
        const row = eventToRow(eventRecord)
        const nextCompleted = scalarNumber(eventRecord.completed)
        const nextTotal = scalarNumber(eventRecord.total)
        const nextRowsWritten = scalarNumber(eventRecord.rows_written)
        const nextFailedCount = scalarNumber(eventRecord.failed_count)
        const nextMatchId = scalarNumber(eventRecord.match_id)
        const nextKind = scalarText(eventRecord.kind) || 'event'
        const nextMessage = scalarText(eventRecord.message) || scalarText(eventRecord.reason) || nextKind
        const nextBrowserPath = scalarText(eventRecord.browser_path)
        const nextError = nextKind === 'error' ? nextMessage : ''

        setEventState((prev) => ({
          ...prev,
          rows: [row, ...prev.rows].slice(0, 500),
          completed: nextCompleted ?? prev.completed,
          total: nextTotal ?? prev.total,
          rowsWritten: nextRowsWritten ?? prev.rowsWritten,
          failedCount: nextFailedCount ?? prev.failedCount,
          currentMatchId: nextMatchId ?? prev.currentMatchId,
          currentKind: nextKind,
          currentMessage: nextMessage,
          browserPath: nextBrowserPath || prev.browserPath,
          lastError: nextError || prev.lastError,
        }))
      },
      onError: (message) => {
        setEventState((prev) => ({
          ...prev,
          isRunning: false,
          currentKind: 'error',
          currentMessage: message,
          lastError: message,
          finishedAt: Date.now(),
        }))
      },
      onDone: () => {
        streamRef.current = null
        setEventState((prev) => ({
          ...prev,
          isRunning: false,
          currentMessage: prev.currentMessage || 'Event stream finished.',
          finishedAt: Date.now(),
        }))
      },
    })

    return true
  }, [])

  const progressPct = clampProgress(eventState.completed, eventState.total)

  const eventFetchValue = useMemo<EventFetchProgressValue>(
    () => ({
      isRunning: eventState.isRunning,
      rows: eventState.rows,
      completed: eventState.completed,
      total: eventState.total,
      progressPct,
      currentMatchId: eventState.currentMatchId,
      currentMessage: eventState.currentMessage,
      currentKind: eventState.currentKind,
      rowsWritten: eventState.rowsWritten,
      failedCount: eventState.failedCount,
      browserPath: eventState.browserPath,
      lastError: eventState.lastError,
      league: eventState.league,
      season: eventState.season,
      nation: eventState.nation,
      tier: eventState.tier,
      start,
      stop,
      clear,
    }),
    [clear, eventState, progressPct, start, stop],
  )

  return (
    <AppConfigContext.Provider value={{ appName: 'WhoScored Match Analysis' }}>
      <EventFetchProgressContext.Provider value={eventFetchValue}>
        {children}
        <FloatingEventFetchProgress state={eventState} progressPct={progressPct} stop={stop} clear={clear} />
      </EventFetchProgressContext.Provider>
    </AppConfigContext.Provider>
  )
}

export function useAppConfig(): AppConfigValue {
  return useContext(AppConfigContext)
}

export function useEventFetchProgress(): EventFetchProgressValue {
  const context = useContext(EventFetchProgressContext)
  if (!context) {
    throw new Error('useEventFetchProgress must be used inside AppProvider')
  }
  return context
}
