import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import Plot from '../lib/PlotlyChart'
import AnalysisRenderProgress, { rememberAnalysisRenderDuration } from '../components/AnalysisRenderProgress'
import DataTable from '../components/DataTable'
import { getLeagueAnalysis, getScheduleFolders, getScheduleSeasons } from '../lib/api'
import type {
  CorrelationMethod,
  LeagueAnalysisResponse,
  LeagueCorrelationPair,
  LeagueMetricCatalogItem,
  LeagueTeamStyleRow,
} from '../lib/api'
import type { TableRow } from '../types/api'

type LeagueTab = 'overview' | 'correlations' | 'pca' | 'clusters' | 'teams' | 'data_quality'

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

const LEAGUE_TABS: Array<{ key: LeagueTab; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'correlations', label: 'Correlations' },
  { key: 'pca', label: 'PCA map' },
  { key: 'clusters', label: 'Style clusters' },
  { key: 'teams', label: 'Team metrics' },
  { key: 'data_quality', label: 'Data quality' },
]

const DEFAULT_X_METRIC = 'possession_proxy'
const DEFAULT_Y_METRIC = 'directness'

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

function n(value: unknown, fallback = 0): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function s(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
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

function MetricCard({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div style={panelStyle({ padding: 14, borderRadius: 16, background: 'rgba(255,255,255,0.035)' })}>
      <div style={miniLabelStyle()}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 950, marginTop: 6 }}>{value}</div>
      {note && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 5, lineHeight: 1.4 }}>{note}</div>}
    </div>
  )
}

function StyleTag({ label }: { label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', border: '1px solid rgba(45,216,233,0.26)', background: 'rgba(45,216,233,0.10)', color: 'var(--accent)', borderRadius: 999, padding: '6px 9px', fontSize: 11, fontWeight: 850 }}>
      {label}
    </span>
  )
}

function metricLabel(metrics: LeagueMetricCatalogItem[], key: string): string {
  return metrics.find((metric) => metric.key === key)?.label ?? key
}

function enabledMetrics(analysis: LeagueAnalysisResponse | null): LeagueMetricCatalogItem[] {
  return countList(analysis?.metric_catalog).filter((metric) => metric.enabled !== false)
}

function scalarRows(rows: LeagueTeamStyleRow[]): TableRow[] {
  return rows.map((row) => {
    const out: TableRow = {}
    Object.entries(row).forEach(([key, value]) => {
      if (Array.isArray(value)) out[key] = value.join(', ')
      else if (typeof value === 'number') out[key] = Number.isInteger(value) ? value : Number(value.toFixed(3))
      else if (typeof value === 'string' || typeof value === 'boolean' || value === null || value === undefined) out[key] = value ?? ''
      else out[key] = String(value)
    })
    return out
  })
}

function plotLayout(title: string, height = 420): Record<string, unknown> {
  return {
    autosize: true,
    height,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e5e7eb', size: 11 },
    margin: { l: 56, r: 24, t: 40, b: 56 },
    title: { text: title, font: { size: 14 } },
    xaxis: { gridcolor: 'rgba(255,255,255,0.08)', zerolinecolor: 'rgba(255,255,255,0.16)' },
    yaxis: { gridcolor: 'rgba(255,255,255,0.08)', zerolinecolor: 'rgba(255,255,255,0.16)' },
  }
}

function PlotCard({ children }: { children: ReactNode }) {
  return <div style={panelStyle({ padding: 12, overflow: 'hidden', borderRadius: 18 })}>{children}</div>
}

function DimensionBars({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const dimensions = countList(analysis.overview?.dimension_scores)
  if (!dimensions.length) return <p style={{ color: 'var(--muted)' }}>No style dimensions available.</p>

  return (
    <PlotCard>
      <Plot
        data={[
          {
            type: 'bar',
            x: dimensions.map((item) => item.label),
            y: dimensions.map((item) => n(item.league_average)),
            hovertemplate: '%{x}<br>Score: %{y:.1f}<extra></extra>',
          },
        ]}
        layout={{ ...plotLayout('League style dimension scores', 360), yaxis: { range: [0, 100], gridcolor: 'rgba(255,255,255,0.08)' } }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%', height: 360 }}
        useResizeHandler
      />
    </PlotCard>
  )
}

function CorrelationHeatmap({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const metrics = enabledMetrics(analysis)
  const matrix = countList(analysis.correlations?.matrix)
  if (!analysis.correlations?.available || !metrics.length || !matrix.length) {
    return <p style={{ color: 'var(--muted)' }}>{analysis.correlations?.note ?? 'Correlation matrix is not available.'}</p>
  }

  const keys = metrics.map((metric) => metric.key)
  const labels = metrics.map((metric) => metric.label)
  const byPair = new Map(matrix.map((item) => [`${item.y}::${item.x}`, item.value]))
  const z = keys.map((yKey) => keys.map((xKey) => n(byPair.get(`${yKey}::${xKey}`), 0)))

  return (
    <PlotCard>
      <Plot
        data={[
          {
            type: 'heatmap',
            x: labels,
            y: labels,
            z,
            zmin: -1,
            zmax: 1,
            colorscale: 'RdBu',
            reversescale: true,
            hovertemplate: '%{y} v %{x}<br>r: %{z:.2f}<extra></extra>',
          },
        ]}
        layout={{ ...plotLayout(`Correlation matrix (${analysis.correlations.method ?? 'pearson'})`, 620), margin: { l: 190, r: 20, t: 44, b: 150 } }}
        config={{ displayModeBar: true, responsive: true }}
        style={{ width: '100%', height: 620 }}
        useResizeHandler
      />
    </PlotCard>
  )
}

function MetricScatter({ analysis, xMetric, yMetric }: { analysis: LeagueAnalysisResponse; xMetric: string; yMetric: string }) {
  const rows = countList(analysis.teams)
  const metrics = countList(analysis.metric_catalog)
  const xLabel = metricLabel(metrics, xMetric)
  const yLabel = metricLabel(metrics, yMetric)

  return (
    <PlotCard>
      <Plot
        data={[
          {
            type: 'scatter',
            mode: 'markers+text',
            x: rows.map((row) => n(row[xMetric])),
            y: rows.map((row) => n(row[yMetric])),
            text: rows.map((row) => s(row.team)),
            textposition: 'top center',
            hovertemplate: `%{text}<br>${xLabel}: %{x:.2f}<br>${yLabel}: %{y:.2f}<extra></extra>`,
            marker: { size: 11, opacity: 0.86 },
          },
        ]}
        layout={{ ...plotLayout(`${xLabel} v ${yLabel}`, 440), xaxis: { title: xLabel, gridcolor: 'rgba(255,255,255,0.08)' }, yaxis: { title: yLabel, gridcolor: 'rgba(255,255,255,0.08)' } }}
        config={{ displayModeBar: true, responsive: true }}
        style={{ width: '100%', height: 440 }}
        useResizeHandler
      />
    </PlotCard>
  )
}

function PcaMap({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const pca = analysis.pca
  if (!pca?.available || !countList(pca.team_scores).length) return <p style={{ color: 'var(--muted)' }}>{pca?.note ?? 'PCA is not available.'}</p>
  const rows = countList(pca.team_scores)
  const variance = countList(pca.explained_variance_pct)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.35fr) minmax(280px, 0.65fr)', gap: 14 }}>
      <PlotCard>
        <Plot
          data={[
            {
              type: 'scatter',
              mode: 'markers+text',
              x: rows.map((row) => row.pc1),
              y: rows.map((row) => row.pc2),
              text: rows.map((row) => row.team),
              textposition: 'top center',
              hovertemplate: '%{text}<br>PC1: %{x:.2f}<br>PC2: %{y:.2f}<extra></extra>',
              marker: { size: 12, opacity: 0.88 },
            },
          ]}
          layout={{ ...plotLayout('PCA team style map', 500), xaxis: { title: `PC1 (${formatValue(variance[0], 1)}%)`, gridcolor: 'rgba(255,255,255,0.08)' }, yaxis: { title: `PC2 (${formatValue(variance[1], 1)}%)`, gridcolor: 'rgba(255,255,255,0.08)' } }}
          config={{ displayModeBar: true, responsive: true }}
          style={{ width: '100%', height: 500 }}
          useResizeHandler
        />
      </PlotCard>
      <div style={panelStyle({ padding: 16, borderRadius: 18 })}>
        <div style={miniLabelStyle()}>PCA axis explanation</div>
        <p style={{ color: 'var(--muted)', lineHeight: 1.55, fontSize: 13 }}>{pca.note}</p>
        {countList(pca.loadings).slice(0, 12).map((item) => (
          <div key={item.metric} style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 9, marginTop: 9 }}>
            <div style={{ fontWeight: 850, fontSize: 12 }}>{item.label ?? item.metric}</div>
            <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 3 }}>PC1 {formatValue(item.pc1, 2)} | PC2 {formatValue(item.pc2, 2)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function PairList({ title, pairs }: { title: string; pairs: LeagueCorrelationPair[] | undefined }) {
  const safePairs = countList(pairs)
  return (
    <div style={panelStyle({ padding: 16, borderRadius: 18 })}>
      <div style={{ fontSize: 15, fontWeight: 900, marginBottom: 10 }}>{title}</div>
      {safePairs.length ? safePairs.map((pair) => (
        <div key={`${pair.x}-${pair.y}`} style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 10, marginTop: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 850 }}>{pair.x_label ?? pair.x} and {pair.y_label ?? pair.y}</div>
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 3 }}>r = {formatValue(pair.value, 2)} | {pair.strength ?? 'relationship'}</div>
        </div>
      )) : <p style={{ color: 'var(--muted)', margin: 0 }}>No relationships found.</p>}
    </div>
  )
}

function Clusters({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const clusters = countList(analysis.clusters?.clusters)
  if (!analysis.clusters?.available || !clusters.length) return <p style={{ color: 'var(--muted)' }}>{analysis.clusters?.note ?? 'Clusters are not available.'}</p>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
      {clusters.map((cluster) => (
        <div key={cluster.cluster} style={panelStyle({ padding: 16, borderRadius: 18 })}>
          <div style={miniLabelStyle()}>Cluster {cluster.cluster + 1}</div>
          <h3 style={{ margin: '6px 0 8px', fontSize: 17 }}>{cluster.label}</h3>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 12 }}>
            {countList(cluster.teams).map((team) => <StyleTag key={team} label={team} />)}
          </div>
          {cluster.centroid && Object.entries(cluster.centroid).slice(0, 8).map(([key, value]) => (
            <div key={key} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 8, marginTop: 8, fontSize: 12 }}>
              <span style={{ color: 'var(--muted)' }}>{key.replace(/_/g, ' ')}</span>
              <strong>{formatValue(value, 1)}</strong>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Overview({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const findings = countList(analysis.findings)
  const dimensions = countList(analysis.overview?.dominant_dimensions)
  const teams = countList(analysis.teams)

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, marginBottom: 18 }}>
        <MetricCard label="Teams compared" value={formatValue(analysis.overview?.teams_compared, 0)} note="After minimum match filter" />
        <MetricCard label="Event rows" value={formatValue(analysis.overview?.event_rows, 0)} note="Saved local rows analysed" />
        <MetricCard label="Event matches" value={formatValue(analysis.overview?.event_matches, 0)} note="Unique event match ids" />
        <MetricCard label="Method" value={s(analysis.overview?.correlation_method, 'pearson')} note="Correlation method" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.25fr) minmax(300px, 0.75fr)', gap: 14, alignItems: 'stretch' }}>
        <DimensionBars analysis={analysis} />
        <div style={panelStyle({ padding: 16, borderRadius: 18 })}>
          <div style={miniLabelStyle()}>League read</div>
          <h3 style={{ margin: '7px 0 10px', fontSize: 18 }}>Dominant style signals</h3>
          <div style={{ display: 'grid', gap: 10 }}>
            {dimensions.map((dimension) => (
              <div key={dimension.key} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 11, background: 'rgba(255,255,255,0.035)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <strong>{dimension.label}</strong>
                  <span style={{ color: 'var(--accent)', fontWeight: 900 }}>{formatValue(dimension.league_average, 1)}</span>
                </div>
                <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 5 }}>
                  Leaders: {countList(dimension.top_teams).map((team) => team.team).join(', ') || 'Not available'}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Section title="Interpretation notes" kicker="Statistical read">
        {findings.length ? (
          <div style={{ display: 'grid', gap: 10 }}>
            {findings.map((finding) => (
              <div key={finding} style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: 14, padding: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
                {finding}
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: 'var(--muted)' }}>No findings available yet.</p>
        )}
      </Section>

      <Section title="Team style tags" kicker="Quick scout read">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
          {teams.slice(0, 12).map((team) => (
            <div key={team.team} style={panelStyle({ padding: 14, borderRadius: 16 })}>
              <div style={{ fontWeight: 900 }}>{team.team}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7, marginTop: 10 }}>
                {countList(team.style_tags as string[] | undefined).map((tag) => <StyleTag key={tag} label={tag} />)}
              </div>
            </div>
          ))}
        </div>
      </Section>
    </>
  )
}

function DataQuality({ analysis }: { analysis: LeagueAnalysisResponse }) {
  const quality = analysis.data_quality
  return (
    <Section title="Data quality" kicker="Validation">
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12, marginBottom: 16 }}>
        <MetricCard label="Source files" value={formatValue(quality?.source_file_count, 0)} />
        <MetricCard label="Schedule" value={quality?.schedule?.available ? 'Loaded' : 'Missing'} note={s(quality?.schedule?.note, '')} />
        <MetricCard label="Cache" value={analysis.render_meta?.cache_hit ? 'Hit' : 'Fresh run'} note={analysis.render_meta?.duration_ms ? `${formatValue(analysis.render_meta.duration_ms, 0)} ms` : undefined} />
      </div>
      <div style={panelStyle({ padding: 16, borderRadius: 18 })}>
        <div style={miniLabelStyle()}>Notes</div>
        {countList(quality?.notes).map((note) => <p key={note} style={{ color: 'var(--muted)', margin: '8px 0', lineHeight: 1.45 }}>{note}</p>)}
        <div style={{ marginTop: 14 }}>
          <div style={miniLabelStyle()}>Model status</div>
          <pre style={{ whiteSpace: 'pre-wrap', color: 'var(--muted)', fontSize: 12, lineHeight: 1.5 }}>{JSON.stringify(quality?.model_quality ?? {}, null, 2)}</pre>
        </div>
        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 850 }}>Source files used</summary>
          <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
            {countList(quality?.source_files).map((file) => <code key={file} style={{ color: 'var(--muted)', fontSize: 12 }}>{file}</code>)}
          </div>
        </details>
      </div>
    </Section>
  )
}

export default function LeagueAnalysisPage() {
  const [folders, setFolders] = useState<Record<string, string[]>>({})
  const nations = useMemo(() => Object.keys(folders).sort(), [folders])
  const [nation, setNation] = useState('')
  const [tier, setTier] = useState('')
  const [season, setSeason] = useState('')
  const [seasons, setSeasons] = useState<string[]>([])
  const [method, setMethod] = useState<CorrelationMethod>('pearson')
  const [minMatches, setMinMatches] = useState(1)
  const [analysis, setAnalysis] = useState<LeagueAnalysisResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<LeagueTab>('overview')
  const [xMetric, setXMetric] = useState(DEFAULT_X_METRIC)
  const [yMetric, setYMetric] = useState(DEFAULT_Y_METRIC)

  useEffect(() => {
    getScheduleFolders()
      .then((data) => {
        setFolders(data)
        const firstNation = Object.keys(data).sort()[0] ?? ''
        const firstTier = firstNation ? [...(data[firstNation] ?? [])].sort()[0] ?? '' : ''
        setNation((prev) => prev || firstNation)
        setTier((prev) => prev || firstTier)
      })
      .catch((err) => setError(asErrorMessage(err)))
  }, [])

  useEffect(() => {
    if (!nation) return
    const available = [...(folders[nation] ?? [])].sort()
    if (!available.includes(tier)) setTier(available[0] ?? '')
  }, [folders, nation, tier])

  useEffect(() => {
    if (!nation || !tier) return
    getScheduleSeasons(nation, tier)
      .then((items) => {
        setSeasons(items)
        setSeason((prev) => (prev && items.includes(prev) ? prev : items[0] ?? ''))
      })
      .catch((err) => setError(asErrorMessage(err)))
  }, [nation, tier])

  useEffect(() => {
    const metrics = enabledMetrics(analysis)
    if (!metrics.find((metric) => metric.key === xMetric)) setXMetric(metrics[0]?.key ?? DEFAULT_X_METRIC)
    if (!metrics.find((metric) => metric.key === yMetric)) setYMetric(metrics[1]?.key ?? metrics[0]?.key ?? DEFAULT_Y_METRIC)
  }, [analysis, xMetric, yMetric])

  async function runAnalysis() {
    if (!nation || !tier || !season) return
    setLoading(true)
    setStartedAt(Date.now())
    setError('')
    try {
      const data = await getLeagueAnalysis({ nation, tier, season, method, min_matches: minMatches })
      setAnalysis(data)
      if (data.render_meta?.duration_ms) rememberAnalysisRenderDuration('league_analysis', data.render_meta.duration_ms)
    } catch (err) {
      setError(asErrorMessage(err))
    } finally {
      setLoading(false)
      setStartedAt(null)
    }
  }

  const tiers = useMemo(() => [...(folders[nation] ?? [])].sort(), [folders, nation])
  const metrics = enabledMetrics(analysis)
  const teamRows = useMemo(() => scalarRows(countList(analysis?.teams)), [analysis])
  const tableColumns = useMemo(() => {
    const preferred = ['team', 'matches', 'possession_proxy', 'passes_per_match', 'directness', 'final_third_entries_per_match', 'box_entries_per_match', 'shots_per_match', 'xg_per_match', 'xt_per_match', 'high_regains_per_match', 'defensive_height', 'wide_action_share', 'set_piece_share', 'style_tags']
    if (!teamRows.length) return []
    const all = Object.keys(teamRows[0])
    return [...preferred.filter((key) => all.includes(key)), ...all.filter((key) => !preferred.includes(key))]
  }, [teamRows])

  return (
    <div style={{ maxWidth: 1480, margin: '0 auto' }}>
      <section className="card" style={{ padding: 22, borderRadius: 22, marginBottom: 18, background: 'linear-gradient(135deg, rgba(45,216,233,0.12), rgba(134,59,255,0.10), rgba(255,255,255,0.035))' }}>
        <div style={miniLabelStyle()}>League Analysis</div>
        <h1 style={{ margin: '8px 0 8px', fontSize: 32, lineHeight: 1.08 }}>League style profiling and statistical analysis</h1>
        <p style={{ color: 'var(--muted)', maxWidth: 980, lineHeight: 1.55, margin: 0 }}>
          Analyse a full saved league season to understand playing style trends, team clusters, outlier profiles and relationships between key style metrics. This uses only the saved event files already in the app.
        </p>
      </section>

      <section style={panelStyle({ padding: 16, marginBottom: 16 })}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12, alignItems: 'end' }}>
          <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
            Nation
            <select value={nation} onChange={(event) => setNation(event.target.value)} style={FIELD_STYLE}>
              {nations.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
            Tier
            <select value={tier} onChange={(event) => setTier(event.target.value)} style={FIELD_STYLE}>
              {tiers.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
            Season
            <select value={season} onChange={(event) => setSeason(event.target.value)} style={FIELD_STYLE}>
              {seasons.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
            Correlation method
            <select value={method} onChange={(event) => setMethod(event.target.value as CorrelationMethod)} style={FIELD_STYLE}>
              <option value="pearson">Pearson</option>
              <option value="spearman">Spearman</option>
              <option value="kendall">Kendall</option>
            </select>
          </label>
          <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
            Minimum matches
            <input value={minMatches} onChange={(event) => setMinMatches(Math.max(1, Number(event.target.value) || 1))} type="number" min={1} max={60} style={FIELD_STYLE} />
          </label>
          <button type="button" disabled={loading || !nation || !tier || !season} onClick={runAnalysis} style={{ ...BUTTON_STYLE, opacity: loading ? 0.65 : 1 }}>
            {loading ? 'Analysing...' : 'Run league analysis'}
          </button>
        </div>
      </section>

      {loading && <AnalysisRenderProgress kind="league_analysis" status="running" startedAt={startedAt} message="Building league style and statistical profile from saved events." style={{ marginBottom: 16 }} />}

      {error && (
        <div className="card" style={{ border: '1px solid rgba(248,113,113,0.28)', color: '#fecaca', marginBottom: 16 }}>
          <strong>League analysis failed</strong>
          <div style={{ marginTop: 6, fontSize: 12, color: '#fca5a5' }}>{error}</div>
        </div>
      )}

      {analysis && (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
            {LEAGUE_TABS.map((item) => (
              <button key={item.key} type="button" onClick={() => setTab(item.key)} style={tab === item.key ? BUTTON_STYLE : SECONDARY_BUTTON_STYLE}>
                {item.label}
              </button>
            ))}
          </div>

          {tab === 'overview' && <Overview analysis={analysis} />}

          {tab === 'correlations' && (
            <>
              <Section
                title="Metric relationship explorer"
                kicker="Correlation and scatter"
                right={
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
                      X metric
                      <select value={xMetric} onChange={(event) => setXMetric(event.target.value)} style={{ ...FIELD_STYLE, width: 220 }}>
                        {metrics.map((metric) => <option key={metric.key} value={metric.key}>{metric.label}</option>)}
                      </select>
                    </label>
                    <label style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 850 }}>
                      Y metric
                      <select value={yMetric} onChange={(event) => setYMetric(event.target.value)} style={{ ...FIELD_STYLE, width: 220 }}>
                        {metrics.map((metric) => <option key={metric.key} value={metric.key}>{metric.label}</option>)}
                      </select>
                    </label>
                  </div>
                }
              >
                <MetricScatter analysis={analysis} xMetric={xMetric} yMetric={yMetric} />
              </Section>

              <Section title="Correlation matrix" kicker="League style relationships">
                <CorrelationHeatmap analysis={analysis} />
              </Section>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14, marginBottom: 18 }}>
                <PairList title="Strongest positive relationships" pairs={analysis.correlations?.strongest_positive} />
                <PairList title="Strongest negative relationships" pairs={analysis.correlations?.strongest_negative} />
              </div>
            </>
          )}

          {tab === 'pca' && (
            <Section title="PCA style map" kicker="Dimensionality reduction">
              <PcaMap analysis={analysis} />
            </Section>
          )}

          {tab === 'clusters' && (
            <>
              <Section title="Style clusters" kicker="Team similarity groups">
                <Clusters analysis={analysis} />
              </Section>
              <Section title="Outlier signals" kicker="Z score scan">
                {countList(analysis.outliers).length ? (
                  <DataTable
                    columns={['team', 'label', 'value', 'z_score', 'league_average', 'direction']}
                    rows={countList(analysis.outliers).map((row) => row as unknown as TableRow)}
                    height={420}
                  />
                ) : <p style={{ color: 'var(--muted)' }}>No major outliers found.</p>}
              </Section>
            </>
          )}

          {tab === 'teams' && (
            <Section title="Team style metric table" kicker="Full league profile">
              <DataTable columns={tableColumns} rows={teamRows} height={620} maxRows={500} />
            </Section>
          )}

          {tab === 'data_quality' && <DataQuality analysis={analysis} />}
        </>
      )}
    </div>
  )
}