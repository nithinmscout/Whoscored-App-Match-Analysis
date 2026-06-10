import { Component, Suspense, lazy, useState, type ErrorInfo, type ReactNode } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppProvider } from './lib/context'

const HomePage = lazy(() => import('./pages/HomePage'))
const ScheduleEventsPage = lazy(() => import('./pages/ScheduleEventsPage'))
const MatchAnalysisPage = lazy(() => import('./pages/MatchAnalysisPage'))
const MatchViewerPage = lazy(() => import('./pages/MatchViewerPage'))
const TeamViewerPage = lazy(() => import('./pages/TeamViewerPage'))
const LeagueAnalysisPage = lazy(() => import('./pages/LeagueAnalysisPage'))
const DebugPage = lazy(() => import('./pages/DebugPage'))

const NAV = [
  { path: '/', label: 'Home', desc: 'Workspace overview', short: 'HM' },
  { path: '/loader', label: 'Schedule and Events', desc: 'Scrape and save data', short: 'SE' },
  { path: '/match-analysis', label: 'Match Analysis', desc: 'Phase dashboard', short: 'MA' },
  { path: '/match-viewer', label: 'Match Viewer', desc: 'Raw fixture rows', short: 'MV' },
  { path: '/team-viewer', label: 'Team Analysis', desc: 'Team phases and players', short: 'TA' },
  { path: '/league-analysis', label: 'League Analysis', desc: 'League style statistics', short: 'LA' },
  { path: '/debug', label: 'Debug', desc: 'Backend health', short: 'DB' },
]

function activePath(current: string, target: string) {
  if (target === '/') return current === '/'
  return current === target || current.startsWith(`${target}/`)
}

class PageErrorBoundary extends Component<{ page: string; children: ReactNode }, { hasError: boolean; message: string }> {
  constructor(props: { page: string; children: ReactNode }) {
    super(props)
    this.state = { hasError: false, message: '' }
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error?.message ?? 'Unknown error' }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Error while rendering ${this.props.page}`, error, info)
  }

  override render() {
    if (!this.state.hasError) return this.props.children
    return (
      <div className="card" style={{ border: '1px solid rgba(248,113,113,0.28)', color: '#fecaca' }}>
        <div style={{ fontSize: 16, fontWeight: 850, marginBottom: 8 }}>{this.props.page} crashed while rendering</div>
        <div style={{ fontSize: 12, color: '#fca5a5' }}>{this.state.message}</div>
      </div>
    )
  }
}

function LazyPage({ label, children }: { label: string; children: ReactNode }) {
  return (
    <PageErrorBoundary page={label}>
      <Suspense fallback={<div className="card" style={{ maxWidth: 420 }}>Loading {label}...</div>}>{children}</Suspense>
    </PageErrorBoundary>
  )
}

function Sidebar({ collapsed, setCollapsed }: { collapsed: boolean; setCollapsed: (value: boolean) => void }) {
  const location = useLocation()

  return (
    <aside
      style={{
        width: collapsed ? 82 : 292,
        minWidth: collapsed ? 82 : 292,
        height: '100vh',
        position: 'sticky',
        top: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        padding: 14,
        borderRight: '1px solid rgba(255,255,255,0.08)',
        background: 'linear-gradient(180deg, rgba(8,13,24,0.98), rgba(5,9,17,0.98))',
        transition: 'width 180ms ease, min-width 180ms ease',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: collapsed ? 'center' : 'space-between', gap: 10, marginBottom: 16 }}>
        {!collapsed && (
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 11, color: 'var(--accent)', letterSpacing: 1.2, textTransform: 'uppercase', fontWeight: 900 }}>WhoScored</div>
            <div style={{ fontSize: 17, fontWeight: 950, marginTop: 3 }}>Match Analysis</div>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          style={{
            width: 42,
            height: 42,
            borderRadius: 14,
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.06)',
            color: 'var(--text)',
            cursor: 'pointer',
            fontWeight: 950,
          }}
        >
          {collapsed ? '›' : '‹'}
        </button>
      </div>

      <nav style={{ display: 'grid', gap: 8 }}>
        {NAV.map((item) => {
          const active = activePath(location.pathname, item.path)
          return (
            <Link
              key={item.path}
              to={item.path}
              title={collapsed ? item.label : undefined}
              style={{
                display: 'grid',
                gridTemplateColumns: collapsed ? '1fr' : '42px 1fr',
                gap: collapsed ? 0 : 10,
                alignItems: 'center',
                textDecoration: 'none',
                color: active ? '#f8fafc' : 'var(--muted)',
                border: `1px solid ${active ? 'rgba(45,216,233,0.38)' : 'rgba(255,255,255,0.07)'}`,
                background: active ? 'rgba(45,216,233,0.12)' : 'rgba(255,255,255,0.03)',
                borderRadius: 15,
                padding: collapsed ? '11px 6px' : '10px',
              }}
            >
              <span
                style={{
                  width: collapsed ? '100%' : 42,
                  height: 34,
                  borderRadius: 12,
                  display: 'grid',
                  placeItems: 'center',
                  background: active ? 'rgba(45,216,233,0.18)' : 'rgba(255,255,255,0.05)',
                  color: active ? 'var(--accent)' : 'var(--muted)',
                  fontSize: 11,
                  fontWeight: 950,
                }}
              >
                {item.short}
              </span>
              {!collapsed && (
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 13, fontWeight: 850 }}>{item.label}</span>
                  <span style={{ display: 'block', fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{item.desc}</span>
                </span>
              )}
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}

function AppRoutes() {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar collapsed={collapsed} setCollapsed={setCollapsed} />
      <main style={{ flex: 1, minWidth: 0, padding: '22px clamp(16px, 2.5vw, 32px)', overflowX: 'hidden' }}>
        <Routes>
          <Route path="/" element={<LazyPage label="Home"><HomePage /></LazyPage>} />
          <Route path="/loader" element={<LazyPage label="Schedule and Events"><ScheduleEventsPage /></LazyPage>} />
          <Route path="/match-analysis" element={<LazyPage label="Match Analysis"><MatchAnalysisPage /></LazyPage>} />
          <Route path="/match-viewer" element={<LazyPage label="Match Viewer"><MatchViewerPage /></LazyPage>} />
          <Route path="/team-viewer" element={<LazyPage label="Team Analysis"><TeamViewerPage /></LazyPage>} />
          <Route path="/league-analysis" element={<LazyPage label="League Analysis"><LeagueAnalysisPage /></LazyPage>} />
          <Route path="/debug" element={<LazyPage label="Debug"><DebugPage /></LazyPage>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AppProvider>
  )
}