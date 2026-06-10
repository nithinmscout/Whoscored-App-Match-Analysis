import { useEffect, useMemo, useState } from 'react'
import DataTable from '../components/DataTable'
import PageLayout from '../components/PageLayout'
import { getMatchAnalysis, getScheduleFolders, getScheduleSeasons, getViewerMatchEvents } from '../lib/api'
import type { MatchAnalysisResponse, TableRow } from '../types/api'

function asErrorMessage(error: unknown): string {
  const anyError = error as { response?: { data?: { detail?: string } }; message?: string }
  return anyError?.response?.data?.detail ?? anyError?.message ?? 'Unknown error'
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

function toTableRows(rows: unknown[]): TableRow[] {
  return rows.map((row) => row as TableRow)
}

export default function MatchViewerPage() {
  const [folders, setFolders] = useState<Record<string, string[]>>({})
  const [nation, setNation] = useState('')
  const [tier, setTier] = useState('')
  const [season, setSeason] = useState('')
  const [seasons, setSeasons] = useState<string[]>([])
  const [analysis, setAnalysis] = useState<MatchAnalysisResponse | null>(null)
  const [selectedMatchId, setSelectedMatchId] = useState<number | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<TableRow[]>([])
  const [fixture, setFixture] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const nationOptions = useMemo(() => Object.keys(folders), [folders])
  const folderOptions = useMemo(() => folders[nation] ?? [], [folders, nation])
  const fixtureRows = useMemo(() => toTableRows(analysis?.fixtures ?? []), [analysis])
  const fixtureColumns = useMemo(() => {
    if (!analysis?.fixtures?.length) return []
    return ['match_id', 'kickoff', 'home_team', 'away_team', 'home_score', 'away_score', 'status', 'has_both_events']
  }, [analysis])

  useEffect(() => {
    getScheduleFolders()
      .then((result) => {
        setFolders(result)
        const firstNation = Object.keys(result)[0] ?? ''
        setNation(firstNation)
        const firstFolder = result[firstNation]?.[0] ?? ''
        const parts = firstFolder.match(/^(.*)\s+(T\d+)$/)
        setTier(parts?.[2] ?? '')
      })
      .catch((err) => setError(asErrorMessage(err)))
  }, [])

  useEffect(() => {
    if (!nation || !tier) return
    getScheduleSeasons(nation, tier)
      .then((result) => {
        setSeasons(result)
        setSeason(result[0] ?? '')
      })
      .catch(() => setSeasons([]))
  }, [nation, tier])

  const handleFolderChange = (folder: string) => {
    const parts = folder.match(/^(.*)\s+(T\d+)$/)
    setNation(parts?.[1] ?? nation)
    setTier(parts?.[2] ?? '')
    setSelectedMatchId(null)
    setAnalysis(null)
    setRows([])
    setColumns([])
  }

  const loadFixtures = async () => {
    if (!nation || !tier || !season) return
    setLoading(true)
    setError('')
    try {
      const result = await getMatchAnalysis({ nation, tier, season })
      setAnalysis(result)
      setSelectedMatchId(result.fixtures?.[0]?.match_id ?? null)
      setRows([])
      setColumns([])
      setFixture(null)
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  const loadMatch = async (matchId = selectedMatchId) => {
    if (!nation || !tier || !season || !matchId) return
    setLoading(true)
    setError('')
    try {
      const result = await getViewerMatchEvents({ nation, tier, season, match_id: Number(matchId), limit: 10000 })
      setColumns(result.columns ?? [])
      setRows(result.rows ?? [])
      setFixture((result.fixture as Record<string, unknown>) ?? null)
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <PageLayout title="Match Viewer" caption="Inspect the combined saved event rows for one fixture without the full dashboard layout.">
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 12 }}>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Nation
            <select value={nation} onChange={(e) => { setNation(e.target.value); setSelectedMatchId(null) }} style={fieldStyle()}>
              {nationOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Folder
            <select value={`${nation} ${tier}`.trim()} onChange={(e) => handleFolderChange(e.target.value)} style={fieldStyle()}>
              {folderOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Season
            <select value={season} onChange={(e) => setSeason(e.target.value)} style={fieldStyle()}>
              {seasons.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)' }}>Match ID
            <input value={selectedMatchId ?? ''} onChange={(e) => setSelectedMatchId(Number(e.target.value) || null)} style={fieldStyle()} />
          </label>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 14 }}>
          <button type="button" onClick={loadFixtures} style={buttonStyle()} disabled={loading}>Load fixtures</button>
          <button type="button" onClick={() => loadMatch()} style={buttonStyle()} disabled={loading || !selectedMatchId}>Load match events</button>
        </div>
      </div>

      {error && <div className="card" style={{ border: '1px solid rgba(248,113,113,0.28)', color: '#fecaca', marginBottom: 16 }}>{error}</div>}

      {fixture && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 850, marginBottom: 8 }}>Selected fixture</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10, fontSize: 12 }}>
            {Object.entries(fixture).map(([key, value]) => (
              <div key={key} style={{ border: '1px solid var(--border)', borderRadius: 11, padding: 10 }}>
                <div style={{ color: 'var(--muted)', marginBottom: 4 }}>{key}</div>
                <div style={{ color: 'var(--text)', fontWeight: 800 }}>{String(value ?? '')}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 16 }}>
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 850 }}>Fixtures</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>Clicking is not required. Copy a match ID into the field above.</div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>{analysis?.fixtures?.length ?? 0} fixtures</div>
          </div>
          <DataTable columns={fixtureColumns} rows={fixtureRows} maxRows={500} height={300} />
        </div>

        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: 850 }}>Raw match event rows</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3 }}>Rows are loaded from both team season CSVs and normalised by the backend.</div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>{rows.length} shown</div>
          </div>
          <DataTable columns={columns} rows={rows} maxRows={10000} height={520} />
        </div>
      </div>
    </PageLayout>
  )
}
