import { useEffect, useMemo, useState } from 'react'
import DataTable from '../components/DataTable'
import PageLayout from '../components/PageLayout'
import {
  api,
  getApiBaseUrl,
  getDebugEnvironment,
  getDebugHealth,
  getDebugMatchAnalysis,
} from '../lib/api'
import type { TableRow } from '../types/api'

type MatchDebugParams = {
  nation: string
  tier: string
  season: string
  match_id: string
  game_state: string
  perspective: string
}

function asErrorMessage(error: unknown): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError?.response?.data?.detail ?? anyError?.message ?? 'Unknown error'
}

function valueToCell(value: unknown): string | number | boolean | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function safeRow(value: unknown, index?: number): TableRow {
  const row: TableRow = {}
  if (typeof index === 'number') row.index = index
  if (!value || typeof value !== 'object') {
    row.value = valueToCell(value)
    return row
  }

  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    if (key === 'traceback') continue
    row[key] = valueToCell(raw)
  }
  return row
}

function rowsFromArray(value: unknown): TableRow[] {
  if (!Array.isArray(value)) return []
  return value.map((item, index) => safeRow(item, index + 1))
}

function objectToRows(value: Record<string, unknown> | null | undefined): TableRow[] {
  if (!value) return []
  return Object.entries(value).map(([key, raw]) => ({
    key,
    value: valueToCell(raw),
  }))
}

function getRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function getNested(value: unknown, path: string[]): unknown {
  let current: unknown = value
  for (const key of path) {
    const currentRecord = getRecord(current)
    if (!currentRecord) return null
    current = currentRecord[key]
  }
  return current
}

function buttonStyle() {
  return {
    border: '1px solid rgba(45,216,233,0.42)',
    background: 'rgba(45,216,233,0.18)',
    color: 'var(--text)',
    borderRadius: 11,
    padding: '9px 12px',
    cursor: 'pointer',
    fontWeight: 800,
    fontSize: 12,
  } as const
}

function subtleButtonStyle() {
  return {
    border: '1px solid var(--border)',
    background: 'rgba(255,255,255,0.04)',
    color: 'var(--text)',
    borderRadius: 11,
    padding: '9px 12px',
    cursor: 'pointer',
    fontWeight: 800,
    fontSize: 12,
  } as const
}

function inputStyle() {
  return {
    width: '100%',
    minWidth: 120,
    border: '1px solid var(--border)',
    background: 'rgba(255,255,255,0.05)',
    color: 'var(--text)',
    borderRadius: 10,
    padding: '9px 10px',
    boxSizing: 'border-box',
    fontWeight: 750,
  } as const
}

function fieldLabelStyle() {
  return {
    display: 'grid',
    gap: 6,
    fontSize: 12,
    color: 'var(--muted)',
    fontWeight: 850,
  } as const
}

function panelTitleStyle() {
  return {
    fontSize: 14,
    fontWeight: 900,
    marginBottom: 10,
  } as const
}

function statusCardStyle(ok: boolean | null) {
  const border = ok === null ? '1px solid var(--border)' : ok ? '1px solid rgba(74,222,128,0.28)' : '1px solid rgba(248,113,113,0.32)'
  const background = ok === null ? 'rgba(255,255,255,0.03)' : ok ? 'rgba(74,222,128,0.08)' : 'rgba(248,113,113,0.08)'
  return {
    border,
    background,
    borderRadius: 14,
    padding: 14,
  } as const
}

function preStyle() {
  return {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    background: 'rgba(0,0,0,0.28)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    padding: 12,
    color: '#dbeafe',
    fontSize: 12,
    lineHeight: 1.5,
    maxHeight: 360,
    overflow: 'auto',
  } as const
}

function detailsStyle() {
  return {
    border: '1px solid var(--border)',
    borderRadius: 14,
    padding: 14,
    background: 'rgba(255,255,255,0.03)',
  } as const
}

function FrameAuditPanel({ title, audit }: { title: string; audit: unknown }) {
  const auditRecord = getRecord(audit)
  if (!auditRecord) return null

  const columnRows = rowsFromArray(auditRecord.column_audit)
  const badRows = rowsFromArray(auditRecord.bad_numeric_samples)
  const sampleRows = rowsFromArray(auditRecord.sample_rows)
  const columns = Array.isArray(auditRecord.columns) ? auditRecord.columns.map(String) : []

  return (
    <details style={detailsStyle()}>
      <summary style={{ cursor: 'pointer', fontWeight: 900 }}>
        {title} · {String(auditRecord.row_count ?? 0)} rows · {String(auditRecord.column_count ?? 0)} columns
      </summary>

      <div style={{ marginTop: 14, display: 'grid', gap: 14 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 850, marginBottom: 8 }}>Column audit</div>
          <DataTable
            columns={['index', 'column', 'dtype', 'missing_like', 'blank_strings', 'pd_na_type', 'invalid_numeric', 'invalid_examples']}
            rows={columnRows}
            maxRows={200}
            height={360}
          />
        </div>

        <div>
          <div style={{ fontSize: 13, fontWeight: 850, marginBottom: 8 }}>Bad numeric samples</div>
          <DataTable
            columns={['index', 'column', 'row_index', 'bad_value', 'team', 'player', 'type', 'period', 'minute', 'event_index']}
            rows={badRows}
            maxRows={80}
            height={260}
          />
        </div>

        <div>
          <div style={{ fontSize: 13, fontWeight: 850, marginBottom: 8 }}>Sample rows</div>
          <DataTable columns={['index', ...columns.slice(0, 18)]} rows={sampleRows} maxRows={12} height={300} />
        </div>
      </div>
    </details>
  )
}

export default function DebugPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null)
  const [environment, setEnvironment] = useState<Record<string, unknown> | null>(null)
  const [statusRows, setStatusRows] = useState<TableRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [matchDebugLoading, setMatchDebugLoading] = useState(false)
  const [matchDebugError, setMatchDebugError] = useState('')
  const [matchDebug, setMatchDebug] = useState<Record<string, unknown> | null>(null)
  const [matchParams, setMatchParams] = useState<MatchDebugParams>({
    nation: 'Belgium',
    tier: 'T1',
    season: '2025',
    match_id: '1904887',
    game_state: 'all',
    perspective: 'home',
  })

  const healthRows = useMemo(() => objectToRows(health), [health])
  const packageRows = useMemo(() => objectToRows((environment?.packages as Record<string, unknown>) ?? null), [environment])
  const envRows = useMemo(() => objectToRows((environment?.env as Record<string, unknown>) ?? null), [environment])

  const stageRows = useMemo(() => {
    const stages = getNested(matchDebug, ['stages'])
    if (!Array.isArray(stages)) return []
    return stages.map((stage, index) => {
      const row = getRecord(stage) ?? {}
      return {
        index: index + 1,
        stage: valueToCell(row.stage),
        ok: valueToCell(row.ok),
        duration_ms: valueToCell(row.duration_ms),
        result_type: valueToCell(row.result_type),
        row_count: valueToCell(row.row_count),
        count: valueToCell(row.count),
        error_type: valueToCell(row.error_type),
        message: valueToCell(row.message),
      }
    })
  }, [matchDebug])

  const firstFailedStage = useMemo(() => getRecord(getNested(matchDebug, ['first_failed_stage'])), [matchDebug])
  const firstFailedTraceback = typeof firstFailedStage?.traceback === 'string' ? firstFailedStage.traceback : ''

  const processedPathRows = useMemo(() => objectToRows(getRecord(getNested(matchDebug, ['processed_paths']))), [matchDebug])
  const activeFilterRows = useMemo(() => objectToRows(getRecord(getNested(matchDebug, ['active_filter']))), [matchDebug])
  const dataSourceRows = useMemo(() => objectToRows(getRecord(getNested(matchDebug, ['data_source']))), [matchDebug])

  const runChecks = async () => {
    setLoading(true)
    setError('')
    try {
      const [healthResult, envResult] = await Promise.all([
        getDebugHealth(),
        getDebugEnvironment(),
      ])
      setHealth(healthResult)
      setEnvironment(envResult)

      const endpoints = [
        ['Root', '/'],
        ['Loader status', '/api/loader/status'],
        ['Analysis status', '/api/analysis/status'],
        ['Spatial status', '/api/spatial/status'],
        ['Viewer status', '/api/viewer/status'],
        ['Debug status', '/api/debug/status'],
      ]

      const results = await Promise.all(endpoints.map(async ([label, path]) => {
        try {
          const res = await api.get(path)
          return { endpoint: label, path, status: res.status, ok: true, message: JSON.stringify(res.data).slice(0, 700) }
        } catch (err) {
          return { endpoint: label, path, status: '', ok: false, message: asErrorMessage(err) }
        }
      }))
      setStatusRows(results as TableRow[])
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const runMatchAnalysisDebug = async () => {
    setMatchDebugLoading(true)
    setMatchDebugError('')
    setMatchDebug(null)
    try {
      const result = await getDebugMatchAnalysis({
        nation: matchParams.nation,
        tier: matchParams.tier,
        season: matchParams.season,
        match_id: matchParams.match_id,
        game_state: matchParams.game_state,
        perspective: matchParams.perspective,
      })
      setMatchDebug(result)
    } catch (err) {
      setMatchDebugError(asErrorMessage(err))
    } finally {
      setMatchDebugLoading(false)
    }
  }

  useEffect(() => {
    runChecks().catch(() => undefined)
  }, [])

  const debugOk = typeof matchDebug?.ok === 'boolean' ? matchDebug.ok : null
  const totalDuration = matchDebug?.total_duration_ms

  return (
    <PageLayout title="Debug" caption="Check backend health, paths, package versions, route availability and match analysis failures.">
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>Backend base URL</div>
            <div style={{ fontSize: 16, fontWeight: 900, marginTop: 4 }}>{getApiBaseUrl()}</div>
          </div>
          <button type="button" onClick={runChecks} style={buttonStyle()} disabled={loading}>Run checks</button>
        </div>
      </div>

      {error && <div className="card" style={{ border: '1px solid rgba(248,113,113,0.28)', color: '#fecaca', marginBottom: 16 }}>{error}</div>}

      <details open className="card" style={{ marginBottom: 16 }}>
        <summary style={{ cursor: 'pointer', fontSize: 15, fontWeight: 950 }}>Match analysis debug expander</summary>

        <div style={{ marginTop: 16, display: 'grid', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12 }}>
            <label style={fieldLabelStyle()}>
              Nation
              <input style={inputStyle()} value={matchParams.nation} onChange={(event) => setMatchParams({ ...matchParams, nation: event.target.value })} />
            </label>
            <label style={fieldLabelStyle()}>
              Tier
              <input style={inputStyle()} value={matchParams.tier} onChange={(event) => setMatchParams({ ...matchParams, tier: event.target.value })} />
            </label>
            <label style={fieldLabelStyle()}>
              Season
              <input style={inputStyle()} value={matchParams.season} onChange={(event) => setMatchParams({ ...matchParams, season: event.target.value })} />
            </label>
            <label style={fieldLabelStyle()}>
              Match id
              <input style={inputStyle()} value={matchParams.match_id} onChange={(event) => setMatchParams({ ...matchParams, match_id: event.target.value })} />
            </label>
            <label style={fieldLabelStyle()}>
              Game state
              <select style={inputStyle()} value={matchParams.game_state} onChange={(event) => setMatchParams({ ...matchParams, game_state: event.target.value })}>
                <option value="all">all</option>
                <option value="level">level</option>
                <option value="winning">winning</option>
                <option value="drawing">drawing</option>
                <option value="losing">losing</option>
              </select>
            </label>
            <label style={fieldLabelStyle()}>
              Perspective
              <select style={inputStyle()} value={matchParams.perspective} onChange={(event) => setMatchParams({ ...matchParams, perspective: event.target.value })}>
                <option value="home">home</option>
                <option value="away">away</option>
              </select>
            </label>
          </div>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button type="button" onClick={runMatchAnalysisDebug} style={buttonStyle()} disabled={matchDebugLoading}>
              {matchDebugLoading ? 'Running match debug...' : 'Run match analysis debug'}
            </button>
            <button
              type="button"
              onClick={() => setMatchParams({ nation: 'Belgium', tier: 'T1', season: '2025', match_id: '1904887', game_state: 'all', perspective: 'home' })}
              style={subtleButtonStyle()}
              disabled={matchDebugLoading}
            >
              Use current failing match
            </button>
          </div>

          {matchDebugError && <div style={statusCardStyle(false)}>{matchDebugError}</div>}

          {matchDebug && (
            <>
              <div style={statusCardStyle(debugOk)}>
                <div style={{ fontSize: 13, color: 'var(--muted)' }}>Result</div>
                <div style={{ fontSize: 18, fontWeight: 950, marginTop: 4 }}>
                  {debugOk ? 'All match analysis stages passed' : `Failed at ${String(firstFailedStage?.stage ?? 'unknown stage')}`}
                </div>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
                  Total debug time: {String(totalDuration ?? '')} ms
                </div>
                {!debugOk && (
                  <div style={{ fontSize: 13, color: '#fecaca', marginTop: 8 }}>
                    {String(firstFailedStage?.error_type ?? '')}: {String(firstFailedStage?.message ?? '')}
                  </div>
                )}
              </div>

              <div>
                <div style={panelTitleStyle()}>Stage results</div>
                <DataTable
                  columns={['index', 'stage', 'ok', 'duration_ms', 'result_type', 'row_count', 'count', 'error_type', 'message']}
                  rows={stageRows}
                  maxRows={120}
                  height={440}
                />
              </div>

              {firstFailedTraceback && (
                <details open style={detailsStyle()}>
                  <summary style={{ cursor: 'pointer', fontWeight: 900 }}>Failing traceback</summary>
                  <pre style={preStyle()}>{firstFailedTraceback}</pre>
                </details>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
                <div>
                  <div style={panelTitleStyle()}>Data source</div>
                  <DataTable columns={['key', 'value']} rows={dataSourceRows} maxRows={80} height={260} />
                </div>
                <div>
                  <div style={panelTitleStyle()}>Active filter</div>
                  <DataTable columns={['key', 'value']} rows={activeFilterRows} maxRows={80} height={260} />
                </div>
                <div>
                  <div style={panelTitleStyle()}>Processed paths</div>
                  <DataTable columns={['key', 'value']} rows={processedPathRows} maxRows={80} height={260} />
                </div>
              </div>

              <div style={{ display: 'grid', gap: 14 }}>
                <FrameAuditPanel title="Loaded prepared events" audit={getNested(matchDebug, ['frame_audits', 'loaded_prepared_events'])} />
                <FrameAuditPanel title="After initial normalise" audit={getNested(matchDebug, ['frame_audits', 'after_initial_normalise'])} />
                <FrameAuditPanel title="After shirt enrichment" audit={getNested(matchDebug, ['frame_audits', 'after_shirt_enrichment'])} />
                <FrameAuditPanel title="After game state filter" audit={getNested(matchDebug, ['frame_audits', 'after_game_state_filter'])} />
              </div>
            </>
          )}
        </div>
      </details>

      <div className="card" style={{ marginBottom: 16 }}>
        <div style={panelTitleStyle()}>Route checks</div>
        <DataTable columns={['endpoint', 'path', 'status', 'ok', 'message']} rows={statusRows} maxRows={50} height={300} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 16 }}>
        <div className="card">
          <div style={panelTitleStyle()}>Health and data paths</div>
          <DataTable columns={['key', 'value']} rows={healthRows} maxRows={80} height={420} />
        </div>
        <div className="card">
          <div style={panelTitleStyle()}>Python packages</div>
          <DataTable columns={['key', 'value']} rows={packageRows} maxRows={80} height={420} />
        </div>
        <div className="card">
          <div style={panelTitleStyle()}>Environment</div>
          <DataTable columns={['key', 'value']} rows={envRows} maxRows={80} height={420} />
        </div>
      </div>
    </PageLayout>
  )
}
