import { useEffect, useMemo, useRef, useState } from 'react'
import DataTable from '../components/DataTable'
import PageLayout from '../components/PageLayout'
import { useEventFetchProgress } from '../lib/context'
import {
  getEventCoverage,
  getEventCoverageOverview,
  getLeaguePresets,
  getScheduleFolders,
  loadScheduleCsv,
  openScheduleStream,
  saveSchedule,
  type EventCoverageAudit,
  type EventCoverageOverview,
  type EventCoverageOverviewRow,
  type LeaguePreset,
} from '../lib/api'
import type { TableRow } from '../types/api'

const FALLBACK_LEAGUE_PRESETS: LeaguePreset[] = [
  { league: 'ENG-Premier League', nation: 'England', tier: 'T1', folder: 'England T1', group: 'Top five leagues', season_mode: 'split', has_custom_fallback: true },
  { league: 'ESP-La Liga', nation: 'Spain', tier: 'T1', folder: 'Spain T1', group: 'Top five leagues', season_mode: 'split', has_custom_fallback: true },
  { league: 'FRA-Ligue 1', nation: 'France', tier: 'T1', folder: 'France T1', group: 'Top five leagues', season_mode: 'split', has_custom_fallback: true },
  { league: 'GER-Bundesliga', nation: 'Germany', tier: 'T1', folder: 'Germany T1', group: 'Top five leagues', season_mode: 'split', has_custom_fallback: true },
  { league: 'ITA-Serie A', nation: 'Italy', tier: 'T1', folder: 'Italy T1', group: 'Top five leagues', season_mode: 'split', has_custom_fallback: true },
  { league: 'ENG-Championship', nation: 'England', tier: 'T2', folder: 'England T2', group: 'England', season_mode: 'split', has_custom_fallback: true },
  { league: 'ENG-League One', nation: 'England', tier: 'T3', folder: 'England T3', group: 'England', season_mode: 'split', has_custom_fallback: true },
  { league: 'ENG-League Two', nation: 'England', tier: 'T4', folder: 'England T4', group: 'England', season_mode: 'split', has_custom_fallback: true },
  { league: 'ESP-Segunda Division', nation: 'Spain', tier: 'T2', folder: 'Spain T2', group: 'Spain', season_mode: 'split', has_custom_fallback: true },
  { league: 'FRA-Ligue 2', nation: 'France', tier: 'T2', folder: 'France T2', group: 'France', season_mode: 'split', has_custom_fallback: true },
  { league: 'GER-2. Bundesliga', nation: 'Germany', tier: 'T2', folder: 'Germany T2', group: 'Germany', season_mode: 'split', has_custom_fallback: true },
  { league: 'GER-3. Liga', nation: 'Germany', tier: 'T3', folder: 'Germany T3', group: 'Germany', season_mode: 'split', has_custom_fallback: true },
  { league: 'ITA-Serie B', nation: 'Italy', tier: 'T2', folder: 'Italy T2', group: 'Italy', season_mode: 'split', has_custom_fallback: true },
  { league: 'NED-Eredivisie', nation: 'Netherlands', tier: 'T1', folder: 'Netherlands T1', group: 'Netherlands', season_mode: 'split', has_custom_fallback: true },
  { league: 'NED-Eerste Divisie', nation: 'Netherlands', tier: 'T2', folder: 'Netherlands T2', group: 'Netherlands', season_mode: 'split', has_custom_fallback: true },
  { league: 'BEL-First Division A', nation: 'Belgium', tier: 'T1', folder: 'Belgium T1', group: 'Belgium', season_mode: 'split', has_custom_fallback: true },
  { league: 'BEL-First Division B', nation: 'Belgium', tier: 'T2', folder: 'Belgium T2', group: 'Belgium', season_mode: 'split', has_custom_fallback: true },
  { league: 'POR-Liga Portugal', nation: 'Portugal', tier: 'T1', folder: 'Portugal T1', group: 'Portugal', season_mode: 'split', has_custom_fallback: true },
  { league: 'POR-Liga Portugal 2', nation: 'Portugal', tier: 'T2', folder: 'Portugal T2', group: 'Portugal', season_mode: 'split', has_custom_fallback: true },
  { league: 'SCO-Premiership', nation: 'Scotland', tier: 'T1', folder: 'Scotland T1', group: 'Scotland', season_mode: 'split', has_custom_fallback: true },
  { league: 'SCO-Championship', nation: 'Scotland', tier: 'T2', folder: 'Scotland T2', group: 'Scotland', season_mode: 'split', has_custom_fallback: true },
  { league: 'TUR-Süper Lig', nation: 'Turkey', tier: 'T1', folder: 'Turkey T1', group: 'Other Europe', season_mode: 'split', has_custom_fallback: true },
  { league: 'SAU-Saudi Pro League', nation: 'Saudi Arabia', tier: 'T1', folder: 'Saudi Arabia T1', group: 'Other markets', season_mode: 'split', has_custom_fallback: true },
  { league: 'BRA-Série A', nation: 'Brazil', tier: 'T1', folder: 'Brazil T1', group: 'South America', season_mode: 'calendar', has_custom_fallback: true },
  { league: 'ARG-Liga Profesional', nation: 'Argentina', tier: 'T1', folder: 'Argentina T1', group: 'South America', season_mode: 'calendar', has_custom_fallback: true },
  { league: 'USA-MLS', nation: 'USA', tier: 'T1', folder: 'USA T1', group: 'Other markets', season_mode: 'calendar', has_custom_fallback: true },
]

const OVERVIEW_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'needs_scrape', label: 'Needs scrape' },
  { key: 'partial', label: 'Partial' },
  { key: 'failed', label: 'Failed' },
  { key: 'pending_fixtures', label: 'Pending fixtures' },
  { key: 'complete', label: 'Complete' },
  { key: 'no_schedule', label: 'No schedule' },
] as const

type ScheduleStatusRow = TableRow & {
  time: string
  kind: string
  stage: string
  message: string
  detail: string
  count: number | null
}

type CoverageState = 'refreshing' | 'ready' | 'not_available' | 'failed'
type OverviewFilterKey = typeof OVERVIEW_FILTERS[number]['key']

function asErrorMessage(error: unknown): string {
  const anyError = error as { response?: { data?: { detail?: string }; status?: number }; message?: string }
  return anyError?.response?.data?.detail ?? anyError?.message ?? 'Unknown error'
}

function isNotFoundError(error: unknown): boolean {
  const anyError = error as { response?: { status?: number } }
  return anyError?.response?.status === 404
}

function fieldStyle() {
  return {
    display: 'block',
    width: '100%',
    marginTop: 6,
    padding: '9px 10px',
    borderRadius: 10,
    border: '1px solid var(--border)',
    background: 'rgba(255,255,255,0.04)',
    color: 'var(--text)',
  } as const
}

function buttonStyle(kind: 'primary' | 'secondary' | 'danger' = 'secondary') {
  const colour = kind === 'primary' ? 'rgba(45,216,233,0.22)' : kind === 'danger' ? 'rgba(248,113,113,0.16)' : 'rgba(255,255,255,0.07)'
  const border = kind === 'primary' ? 'rgba(45,216,233,0.42)' : kind === 'danger' ? 'rgba(248,113,113,0.35)' : 'var(--border)'
  return {
    border: `1px solid ${border}`,
    background: colour,
    color: 'var(--text)',
    borderRadius: 11,
    padding: '9px 12px',
    cursor: 'pointer',
    fontWeight: 800,
    fontSize: 12,
  } as const
}

function compactButtonStyle() {
  return {
    ...buttonStyle('secondary'),
    padding: '7px 9px',
    whiteSpace: 'nowrap',
  } as const
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function scalarNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function coverageMetric(audit: EventCoverageAudit | null, key: string): number {
  const value = audit?.counts?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function overviewSummaryMetric(overview: EventCoverageOverview | null, key: string): number {
  const value = overview?.summary?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function normaliseFolderName(nation: string, tier: string): string {
  return `${nation} ${tier}`.trim()
}

function getRecommendedSeason(preset: LeaguePreset | undefined, currentSeason: string): string {
  const trimmed = currentSeason.trim()
  if (preset?.season_mode !== 'calendar') return trimmed || '2025'

  const currentYear = String(new Date().getFullYear())
  if (!trimmed || trimmed === '2025') return currentYear
  return trimmed
}

function selectedCoverageLabel(state: CoverageState, isLoading: boolean): string {
  if (isLoading) return 'Refreshing'
  if (state === 'ready') return 'Ready'
  if (state === 'failed') return 'Failed'
  return 'Not available'
}

function selectedCoverageColour(state: CoverageState, isLoading: boolean): string {
  if (isLoading) return 'var(--accent)'
  if (state === 'ready') return 'var(--accent)'
  if (state === 'failed') return '#fecaca'
  return 'var(--muted)'
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    complete: 'Complete',
    needs_scrape: 'Needs scrape',
    partial: 'Partial',
    failed_only: 'Failed only',
    pending_fixtures: 'Pending fixtures',
    no_schedule: 'No schedule',
    audit_failed: 'Audit failed',
  }
  return labels[status] ?? status
}

function fetchButtonText(coverageLoading: boolean, audit: EventCoverageAudit | null): string {
  if (coverageLoading) return 'Checking coverage'
  const toFetch = coverageMetric(audit, 'to_fetch_now')
  if (toFetch <= 0) return 'Nothing to fetch'
  return `Fetch ${toFetch} matches`
}

function sortOverviewRows(rows: EventCoverageOverviewRow[]): EventCoverageOverviewRow[] {
  return [...rows].sort((a, b) => {
    if (a.priority !== b.priority) return a.priority - b.priority
    if (a.to_fetch_now !== b.to_fetch_now) return b.to_fetch_now - a.to_fetch_now
    if (a.coverage_pct !== b.coverage_pct) return a.coverage_pct - b.coverage_pct
    return `${a.league} ${a.season}`.localeCompare(`${b.league} ${b.season}`)
  })
}

export default function ScheduleEventsPage() {
  const [leaguePresets, setLeaguePresets] = useState<LeaguePreset[]>(FALLBACK_LEAGUE_PRESETS)
  const [league, setLeague] = useState('BEL-First Division A')
  const [season, setSeason] = useState('2025')
  const [nation, setNation] = useState('Belgium')
  const [tier, setTier] = useState('T1')
  const [browserpath, setBrowserpath] = useState('')
  const [headless, setHeadless] = useState(true)
  const [onlyFinished, setOnlyFinished] = useState(true)
  const [overwrite, setOverwrite] = useState(false)
  const [retryFailed, setRetryFailed] = useState(false)
  const [failFast, setFailFast] = useState(true)
  const [scrapePositions, setScrapePositions] = useState(true)

  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<TableRow[]>([])
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [scheduleStreaming, setScheduleStreaming] = useState(false)
  const [scheduleStatusRows, setScheduleStatusRows] = useState<ScheduleStatusRow[]>([])
  const [eventCoverage, setEventCoverage] = useState<EventCoverageAudit | null>(null)
  const [coverageLoading, setCoverageLoading] = useState(false)
  const [coverageState, setCoverageState] = useState<CoverageState>('not_available')
  const [coverageOverview, setCoverageOverview] = useState<EventCoverageOverview | null>(null)
  const [overviewLoading, setOverviewLoading] = useState(false)
  const [overviewError, setOverviewError] = useState('')
  const [overviewFilter, setOverviewFilter] = useState<OverviewFilterKey>('all')
  const [overviewSearch, setOverviewSearch] = useState('')
  const [folders, setFolders] = useState<Record<string, string[]>>({})
  const scheduleStreamRef = useRef<EventSource | null>(null)
  const previousEventStreamingRef = useRef(false)
  const coverageRequestRef = useRef(0)
  const overviewRequestRef = useRef(0)
  const eventFetch = useEventFetchProgress()

  const scheduleStatusColumns = useMemo(() => ['time', 'kind', 'stage', 'message', 'detail', 'count'], [])
  const streamColumns = useMemo(() => ['time', 'kind', 'message', 'match_id', 'completed', 'total', 'rows_written', 'failed_count'], [])
  const coverageColumns = useMemo(
    () => eventCoverage?.columns?.length
      ? eventCoverage.columns
      : ['match_id', 'kickoff', 'home_team', 'away_team', 'finished', 'home_saved', 'away_saved', 'data_status', 'failed_logged', 'scrape_status', 'failure_reason'],
    [eventCoverage],
  )
  const failedCoverageColumns = useMemo(() => ['match_id', 'home_team', 'away_team', 'data_status', 'scrape_status', 'failure_reason'], [])
  const coverageRows = useMemo(() => eventCoverage?.rows ?? [], [eventCoverage])
  const failedCoverageRows = useMemo(() => eventCoverage?.failed_rows ?? [], [eventCoverage])
  const streaming = eventFetch.isRunning
  const streamRows = eventFetch.rows
  const latestScheduleStatus = scheduleStatusRows[0]?.message ?? 'No schedule scrape has started yet.'

  const selectedPreset = useMemo(
    () => leaguePresets.find((item) => item.league === league),
    [league, leaguePresets],
  )
  const targetFolder = selectedPreset?.folder || normaliseFolderName(nation, tier)
  const existingFolderMatched = Boolean(folders[nation]?.includes(targetFolder))
  const groupedPresets = useMemo(() => {
    const groups = new Map<string, LeaguePreset[]>()
    for (const preset of leaguePresets) {
      const group = preset.group || 'Other leagues'
      groups.set(group, [...(groups.get(group) ?? []), preset])
    }
    return Array.from(groups.entries())
  }, [leaguePresets])

  const filteredOverviewRows = useMemo(() => {
    const search = overviewSearch.trim().toLowerCase()
    const baseRows = sortOverviewRows(coverageOverview?.rows ?? [])
    return baseRows.filter((row) => {
      const matchesFilter = overviewFilter === 'all'
        || row.status === overviewFilter
        || (overviewFilter === 'failed' && (row.failed_matches > 0 || row.status === 'audit_failed' || row.status === 'failed_only'))
      const haystack = `${row.league} ${row.folder} ${row.group} ${row.nation} ${row.tier} ${row.season}`.toLowerCase()
      const matchesSearch = !search || haystack.includes(search)
      return matchesFilter && matchesSearch
    })
  }, [coverageOverview, overviewFilter, overviewSearch])

  const applyLeaguePreset = (nextLeague: string, presets = leaguePresets) => {
    const preset = presets.find((item) => item.league === nextLeague)
    setLeague(nextLeague)
    if (preset) {
      setNation(preset.nation)
      setTier(preset.tier)
      setSeason((current) => getRecommendedSeason(preset, current))
    }
  }

  const refreshFolders = async () => {
    try {
      setFolders(await getScheduleFolders())
    } catch {
      setFolders({})
    }
  }

  const refreshSelectedCoverage = async (options?: { showMessage?: boolean; showError?: boolean }): Promise<EventCoverageAudit | null> => {
    if (!league.trim() || !season.trim()) {
      setEventCoverage(null)
      setCoverageState('not_available')
      return null
    }

    const requestId = coverageRequestRef.current + 1
    coverageRequestRef.current = requestId
    setCoverageLoading(true)
    setCoverageState('refreshing')
    if (options?.showError !== false) setError('')

    try {
      const result = await getEventCoverage({
        league,
        season,
        nation,
        tier,
        only_finished: onlyFinished,
        overwrite,
        retry_failed: retryFailed,
      })
      if (requestId === coverageRequestRef.current) {
        setEventCoverage(result)
        setCoverageState('ready')
        if (options?.showMessage) setMessage(result.message || 'Event coverage audit is ready.')
      }
      return result
    } catch (err) {
      if (requestId === coverageRequestRef.current) {
        setEventCoverage(null)
        setCoverageState(isNotFoundError(err) ? 'not_available' : 'failed')
        if (options?.showError !== false && !isNotFoundError(err)) setError(asErrorMessage(err))
      }
      return null
    } finally {
      if (requestId === coverageRequestRef.current) setCoverageLoading(false)
    }
  }

  const refreshCoverageOverview = async (options?: { showError?: boolean }) => {
    const requestId = overviewRequestRef.current + 1
    overviewRequestRef.current = requestId
    setOverviewLoading(true)
    if (options?.showError !== false) setOverviewError('')

    try {
      const result = await getEventCoverageOverview({
        season: season.trim(),
        only_finished: onlyFinished,
        overwrite,
        retry_failed: retryFailed,
      })
      if (requestId === overviewRequestRef.current) {
        setCoverageOverview(result)
        setOverviewError('')
      }
    } catch (err) {
      if (requestId === overviewRequestRef.current) {
        setCoverageOverview(null)
        if (options?.showError !== false) setOverviewError(asErrorMessage(err))
      }
    } finally {
      if (requestId === overviewRequestRef.current) setOverviewLoading(false)
    }
  }

  useEffect(() => {
    getScheduleFolders().then(setFolders).catch(() => undefined)
    getLeaguePresets()
      .then((items) => {
        if (!items.length) return
        setLeaguePresets(items)
        const currentPreset = items.find((item) => item.league === league)
        if (currentPreset) {
          setNation(currentPreset.nation)
          setTier(currentPreset.tier)
        }
      })
      .catch(() => undefined)

    return () => {
      scheduleStreamRef.current?.close()
    }
  }, [])

  useEffect(() => {
    if (scheduleStreaming || streaming) return undefined

    const timer = window.setTimeout(() => {
      refreshSelectedCoverage({ showError: false }).catch(() => undefined)
      refreshCoverageOverview({ showError: false }).catch(() => undefined)
    }, 450)

    return () => window.clearTimeout(timer)
  }, [league, season, nation, tier, onlyFinished, overwrite, retryFailed, scheduleStreaming, streaming])

  useEffect(() => {
    if (previousEventStreamingRef.current && !streaming) {
      refreshFolders().catch(() => undefined)
      refreshSelectedCoverage({ showError: false }).catch(() => undefined)
      refreshCoverageOverview({ showError: false }).catch(() => undefined)
    }
    previousEventStreamingRef.current = streaming
  }, [streaming])

  const refreshAfterLocalScheduleChange = async () => {
    await refreshFolders()
    if (!scheduleStreaming && !streaming) {
      await Promise.all([
        refreshSelectedCoverage({ showError: false }),
        refreshCoverageOverview({ showError: false }),
      ])
    }
  }

  const handleScrapeSchedule = () => {
    if (scheduleStreaming) return
    setLoading(true)
    setScheduleStreaming(true)
    setError('')
    setMessage('')
    setScheduleStatusRows([])

    scheduleStreamRef.current = openScheduleStream({
      league,
      season,
      headless,
      browserpath,
      onEvent: (event) => {
        const eventRecord = event as Record<string, unknown>
        const row: ScheduleStatusRow = {
          time: new Date().toLocaleTimeString(),
          kind: scalarText(eventRecord.kind) || 'status',
          stage: scalarText(eventRecord.stage),
          message: scalarText(eventRecord.message),
          detail: scalarText(eventRecord.reason) || scalarText(eventRecord.page_title) || scalarText(eventRecord.url) || scalarText(eventRecord.available_seasons),
          count: scalarNumber(eventRecord.count),
        }
        setScheduleStatusRows((prev) => [row, ...prev].slice(0, 300))

        if (eventRecord.kind === 'complete') {
          const result = event as { columns?: string[]; rows?: TableRow[]; count?: number; message?: string }
          setColumns(Array.isArray(result.columns) ? result.columns : [])
          setRows(Array.isArray(result.rows) ? result.rows : [])
          setMessage(String(result.message ?? `Loaded ${result.count ?? 0} schedule rows. Review them, then save the schedule.`))
        }

        if (eventRecord.kind === 'error' && eventRecord.stage === 'failed') {
          setError(scalarText(eventRecord.message) || 'Schedule scrape failed.')
        }
      },
      onError: (text) => {
        setError(text)
      },
      onDone: () => {
        setScheduleStreaming(false)
        setLoading(false)
      },
    })
  }

  const handleStopSchedule = () => {
    scheduleStreamRef.current?.close()
    scheduleStreamRef.current = null
    setScheduleStreaming(false)
    setLoading(false)
    setMessage('Schedule stream stopped locally.')
  }

  const handleSaveSchedule = async () => {
    if (!rows.length) {
      setError('No schedule rows are loaded yet.')
      return
    }
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const result = await saveSchedule({ nation, tier, season, rows, league })
      if (result.nation) setNation(result.nation)
      if (result.tier) setTier(result.tier)
      setMessage(result.folder ? `${result.message} Folder: ${result.folder}` : result.message)
      await refreshAfterLocalScheduleChange()
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const handleLoadSaved = async () => {
    setLoading(true)
    setError('')
    setMessage('')
    try {
      const result = await loadScheduleCsv({ nation, tier, season })
      setColumns(result.columns ?? [])
      setRows(result.rows ?? [])
      setMessage(`Loaded ${result.count ?? 0} saved schedule rows from ${targetFolder}.`)
      await refreshAfterLocalScheduleChange()
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const handleSelectOverviewRow = async (row: EventCoverageOverviewRow) => {
    setLeague(row.league)
    setNation(row.nation)
    setTier(row.tier)
    setSeason(row.season)
    setMessage(`Selected ${row.league} ${row.season}.`)

    if (!row.has_schedule) {
      setColumns([])
      setRows([])
      return
    }

    setLoading(true)
    setError('')
    try {
      const result = await loadScheduleCsv({ nation: row.nation, tier: row.tier, season: row.season })
      setColumns(result.columns ?? [])
      setRows(result.rows ?? [])
      setMessage(`Selected ${row.league} and loaded ${result.count ?? 0} saved schedule rows.`)
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const handleStartEvents = async () => {
    if (streaming || coverageLoading) return
    setError('')
    setMessage('Checking event coverage before scraping.')

    const audit = await refreshSelectedCoverage({ showMessage: true, showError: true })
    if (!audit) return

    const toFetch = coverageMetric(audit, 'to_fetch_now')
    if (toFetch <= 0) {
      setMessage(audit.message || 'Nothing to fetch. Current event coverage is already complete for this mode.')
      return
    }

    const started = eventFetch.start({
      league,
      season,
      nation,
      tier,
      headless,
      browserpath,
      only_finished: onlyFinished,
      overwrite,
      retry_failed: retryFailed,
      fail_fast: failFast,
      scrape_positions: scrapePositions,
    })

    if (!started) {
      setMessage('An event fetch is already running.')
    }
  }

  const handleStopEvents = () => {
    eventFetch.stop()
    setMessage('Event stream stopped locally.')
  }

  return (
    <PageLayout title="Schedule and Events Scraper" caption="Scrape schedules, save them, then stream match events into your standalone data folder.">
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 900 }}>League, season and scrape controls</div>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>
              Pick a preset, scrape or load a schedule, then fetch only the event matches that are actually missing.
            </div>
          </div>
          <button type="button" style={buttonStyle()} onClick={() => refreshCoverageOverview()} disabled={overviewLoading || scheduleStreaming || streaming}>
            {overviewLoading ? 'Refreshing overview' : 'Refresh overview'}
          </button>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: 12 }}>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>League preset
            <select value={league} onChange={(event) => applyLeaguePreset(event.target.value)} style={fieldStyle()}>
              {groupedPresets.map(([groupName, items]) => (
                <optgroup key={groupName} label={groupName}>
                  {items.map((item) => (
                    <option key={item.league} value={item.league}>{item.league}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Season
            <input value={season} onChange={(event) => setSeason(event.target.value)} style={fieldStyle()} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Nation folder
            <input value={nation} onChange={(event) => setNation(event.target.value)} style={fieldStyle()} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Tier
            <input value={tier} onChange={(event) => setTier(event.target.value)} style={fieldStyle()} />
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Browser path optional
            <input value={browserpath} onChange={(event) => setBrowserpath(event.target.value)} placeholder="Leave blank to auto detect" style={fieldStyle()} />
          </label>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, marginTop: 14 }}>
          <div style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10 }}>
            <div style={{ color: 'var(--muted)', fontSize: 11, fontWeight: 800, textTransform: 'uppercase', letterSpacing: 0.6 }}>Selected folder</div>
            <div style={{ marginTop: 4, fontSize: 14, fontWeight: 900 }}>{targetFolder || 'No folder selected'}</div>
            <div style={{ marginTop: 3, color: existingFolderMatched ? 'var(--accent)' : 'var(--muted)', fontSize: 12 }}>
              {existingFolderMatched ? 'Matched an existing saved folder.' : 'This folder will be created automatically when you save the schedule or fetch events.'}
            </div>
          </div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10 }}>
            <div style={{ color: 'var(--muted)', fontSize: 11, fontWeight: 800, textTransform: 'uppercase', letterSpacing: 0.6 }}>Season type</div>
            <div style={{ marginTop: 4, fontSize: 14, fontWeight: 900 }}>{selectedPreset?.season_mode === 'calendar' ? 'Calendar year' : 'Split season'}</div>
            <div style={{ marginTop: 3, color: 'var(--muted)', fontSize: 12 }}>
              {selectedPreset?.season_mode === 'calendar' ? 'Use 2025 or 2026 for Brazil, Argentina and MLS.' : 'Use 2025 for the 2025 to 2026 European season.'}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, marginTop: 14, color: 'var(--muted)', fontSize: 12 }}>
          <label><input type="checkbox" checked={headless} onChange={(event) => setHeadless(event.target.checked)} /> Headless</label>
          <label><input type="checkbox" checked={onlyFinished} onChange={(event) => setOnlyFinished(event.target.checked)} /> Finished matches only</label>
          <label><input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} /> Overwrite existing event files</label>
          <label><input type="checkbox" checked={retryFailed} onChange={(event) => setRetryFailed(event.target.checked)} /> Retry failed only</label>
          <label><input type="checkbox" checked={failFast} onChange={(event) => setFailFast(event.target.checked)} /> Stop on first failure</label>
          <label><input type="checkbox" checked={scrapePositions} onChange={(event) => setScrapePositions(event.target.checked)} /> Scrape positions</label>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }}>
          {!scheduleStreaming ? (
            <button type="button" style={buttonStyle('primary')} onClick={handleScrapeSchedule} disabled={loading}>Scrape schedule</button>
          ) : (
            <button type="button" style={buttonStyle('danger')} onClick={handleStopSchedule}>Stop schedule stream</button>
          )}
          <button type="button" style={buttonStyle()} onClick={handleLoadSaved} disabled={loading}>Load saved schedule</button>
          <button type="button" style={buttonStyle()} onClick={handleSaveSchedule} disabled={loading || rows.length === 0}>Save schedule</button>
          {!streaming ? (
            <button type="button" style={buttonStyle('primary')} onClick={handleStartEvents} disabled={coverageLoading}>
              {fetchButtonText(coverageLoading, eventCoverage)}
            </button>
          ) : (
            <button type="button" style={buttonStyle('danger')} onClick={handleStopEvents}>Stop event stream</button>
          )}
        </div>
      </div>

      {error && <div className="card" style={{ border: '1px solid rgba(248,113,113,0.28)', color: '#fecaca', marginBottom: 16 }}>{error}</div>}
      {message && <div className="card" style={{ border: '1px solid rgba(45,216,233,0.24)', color: 'var(--accent)', marginBottom: 16 }}>{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 900 }}>League event coverage overview</div>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 3 }}>
              {coverageOverview?.message ?? 'Local schedule and event files are audited automatically. No WhoScored pages are opened here.'}
            </div>
            {overviewError && <div style={{ color: '#fecaca', fontSize: 12, marginTop: 6 }}>{overviewError}</div>}
          </div>
          <div style={{ fontSize: 12, color: overviewLoading ? 'var(--accent)' : 'var(--muted)', fontWeight: 850 }}>
            {overviewLoading ? 'Refreshing' : 'Ready'}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 10, marginBottom: 12 }}>
          {[
            ['Saved schedules', overviewSummaryMetric(coverageOverview, 'saved_schedules')],
            ['Complete', overviewSummaryMetric(coverageOverview, 'complete')],
            ['Needs scrape', overviewSummaryMetric(coverageOverview, 'needs_scrape')],
            ['Partial', overviewSummaryMetric(coverageOverview, 'partial')],
            ['Failed match leagues', overviewSummaryMetric(coverageOverview, 'failed_match_leagues')],
            ['Unfinished fixtures', overviewSummaryMetric(coverageOverview, 'pending_fixtures')],
            ['No schedule', overviewSummaryMetric(coverageOverview, 'no_schedule')],
          ].map(([label, value]) => (
            <div key={String(label)} style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 850, textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</div>
              <div style={{ marginTop: 5, fontSize: 18, fontWeight: 950 }}>{value}</div>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          {OVERVIEW_FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              style={buttonStyle(overviewFilter === item.key ? 'primary' : 'secondary')}
              onClick={() => setOverviewFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
          <input
            value={overviewSearch}
            onChange={(event) => setOverviewSearch(event.target.value)}
            placeholder="Search league or folder"
            style={{ ...fieldStyle(), width: 230, marginTop: 0 }}
          />
        </div>

        <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 14 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980, fontSize: 12 }}>
            <thead>
              <tr style={{ color: 'var(--muted)', textAlign: 'left', background: 'rgba(255,255,255,0.035)' }}>
                {['League', 'Season', 'Folder', 'Status', 'Finished', 'Complete', 'Missing', 'Partial', 'Failed', 'Not completed', 'To scrape', 'Coverage', 'Action'].map((heading) => (
                  <th key={heading} style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredOverviewRows.map((row) => (
                <tr key={`${row.league}-${row.folder}-${row.season}`}>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)', fontWeight: 850 }}>{row.league}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.season}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.folder}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)', fontWeight: 850 }}>{statusText(row.status)}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.finished_matches}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.with_both_team_events}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.with_no_saved_events}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.with_one_team_events}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.failed_matches}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.not_completed_matches}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)', fontWeight: 850 }}>{row.to_fetch_now}</td>
                  <td style={{ padding: '9px 10px', borderBottom: '1px solid var(--border)' }}>{row.coverage_pct}%</td>
                  <td style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)' }}>
                    <button type="button" style={compactButtonStyle()} onClick={() => handleSelectOverviewRow(row)}>
                      Select league
                    </button>
                  </td>
                </tr>
              ))}
              {!filteredOverviewRows.length && (
                <tr>
                  <td colSpan={13} style={{ padding: 14, color: 'var(--muted)' }}>No leagues match the current filter.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 850 }}>Selected league event coverage</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>
              {eventCoverage?.message ?? 'Coverage refreshes automatically when a league, season or scrape mode changes.'}
            </div>
          </div>
          <div style={{ fontSize: 12, color: selectedCoverageColour(coverageState, coverageLoading), fontWeight: 850 }}>
            {selectedCoverageLabel(coverageState, coverageLoading)}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 10, marginBottom: 12 }}>
          {[
            ['Schedule matches', coverageMetric(eventCoverage, 'matches_in_schedule')],
            ['Finished', coverageMetric(eventCoverage, 'finished_matches')],
            ['Both teams saved', coverageMetric(eventCoverage, 'with_both_team_events')],
            ['Partial data', coverageMetric(eventCoverage, 'with_one_team_events')],
            ['No data', coverageMetric(eventCoverage, 'with_no_saved_events')],
            ['Failed log', coverageMetric(eventCoverage, 'failed_logged')],
            ['To scrape now', coverageMetric(eventCoverage, 'to_fetch_now')],
          ].map(([label, value]) => (
            <div key={String(label)} style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10, background: 'rgba(255,255,255,0.035)' }}>
              <div style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 850, textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</div>
              <div style={{ marginTop: 5, fontSize: 18, fontWeight: 950 }}>{value}</div>
            </div>
          ))}
        </div>

        <DataTable columns={coverageColumns} rows={coverageRows} maxRows={800} height={300} />

        {failedCoverageRows.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 850, marginBottom: 8 }}>Previous failures</div>
            <DataTable columns={failedCoverageColumns} rows={failedCoverageRows} maxRows={250} height={220} />
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 850 }}>Schedule scrape status</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>
              {latestScheduleStatus}
            </div>
          </div>
          <div style={{ fontSize: 12, color: scheduleStreaming ? 'var(--accent)' : 'var(--muted)', fontWeight: 850 }}>
            {scheduleStreaming ? 'Running' : 'Idle'}
          </div>
        </div>
        <DataTable columns={scheduleStatusColumns} rows={scheduleStatusRows} maxRows={300} height={240} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)', gap: 16 }}>
        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 850, marginBottom: 10 }}>Schedule preview</div>
          <DataTable columns={columns} rows={rows} maxRows={600} height={420} />
        </div>
        <div className="card">
          <div style={{ fontSize: 14, fontWeight: 850, marginBottom: 10 }}>Saved schedule folders</div>
          <div style={{ display: 'grid', gap: 8 }}>
            {Object.entries(folders).map(([folderNation, folderNames]) => (
              <div key={folderNation} style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 850 }}>{folderNation}</div>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 4 }}>{folderNames.join(', ')}</div>
              </div>
            ))}
            {!Object.keys(folders).length && <div style={{ color: 'var(--muted)', fontSize: 12 }}>No saved schedules found yet.</div>}
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 10 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 850 }}>Event stream log</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>
              This log is now app wide. You can move to match analysis, viewer or debug while the top right progress popup keeps updating.
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{ fontSize: 12, color: streaming ? 'var(--accent)' : 'var(--muted)', fontWeight: 850 }}>
              {streaming ? 'Running' : 'Idle'}
            </div>
            <button type="button" style={buttonStyle()} onClick={eventFetch.clear} disabled={streaming && !streamRows.length}>
              Clear log
            </button>
          </div>
        </div>
        <DataTable columns={streamColumns} rows={streamRows} maxRows={500} height={360} />
      </div>
    </PageLayout>
  )
}
