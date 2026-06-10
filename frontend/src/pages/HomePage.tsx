import { Link } from 'react-router-dom'

const CARDS = [
  {
    path: '/loader',
    title: 'Schedule and Events',
    desc: 'Scrape fixtures, save schedule CSVs and stream event files into the same folder structure used by the match analysis pipeline.',
  },
  {
    path: '/match-analysis',
    title: 'Match Analysis',
    desc: 'Select a fixture and review the full team level dashboard, including territory, momentum, shots, goalmouth views and raw event rows.',
  },
  {
    path: '/match-viewer',
    title: 'Match Viewer',
    desc: 'Inspect raw combined events for one selected fixture, with quick match metadata and event table validation.',
  },
  {
    path: '/team-viewer',
    title: 'Team Viewer',
    desc: 'Open a team season CSV, filter by match, event type or player, and check event distribution across the squad.',
  },
  {
    path: '/league-analysis',
    title: 'League Analysis',
    desc: 'Run correlation, PCA, clustering and outlier checks across a saved league season to understand playing style trends.',
  },
  {
    path: '/debug',
    title: 'Debug',
    desc: 'Check backend health, data roots, browser detection, package versions and CSV counts before running heavier jobs.',
  },
]

export default function HomePage() {
  return (
    <div style={{ maxWidth: 1180, margin: '0 auto' }}>
      <section
        className="card"
        style={{
          padding: 28,
          borderRadius: 22,
          background: 'linear-gradient(135deg, rgba(45,216,233,0.13), rgba(134,59,255,0.11), rgba(255,255,255,0.035))',
        }}
      >
        <div style={{ color: 'var(--accent)', fontSize: 11, fontWeight: 900, letterSpacing: 1.1, textTransform: 'uppercase' }}>
          Standalone match analysis site
        </div>
        <h1 style={{ margin: '8px 0 10px', fontSize: 34, lineHeight: 1.08, maxWidth: 820 }}>
          A focused WhoScored event workspace for scraping, checking and analysing matches.
        </h1>
        <p style={{ color: 'var(--muted)', fontSize: 14, lineHeight: 1.6, maxWidth: 840 }}>
          This version keeps the same schedule files, event file structure, match calculation logic, xT pipeline and spatial endpoints, but removes the wider player scouting app from the workflow.
        </p>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 14, marginTop: 18 }}>
        {CARDS.map((card) => (
          <Link
            key={card.path}
            to={card.path}
            className="card"
            style={{
              textDecoration: 'none',
              color: 'var(--text)',
              borderRadius: 18,
              padding: 18,
              minHeight: 162,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'space-between',
            }}
          >
            <div>
              <div style={{ fontSize: 17, fontWeight: 900, marginBottom: 8 }}>{card.title}</div>
              <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.55 }}>{card.desc}</div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 800, marginTop: 16 }}>Open page</div>
          </Link>
        ))}
      </div>
    </div>
  )
}