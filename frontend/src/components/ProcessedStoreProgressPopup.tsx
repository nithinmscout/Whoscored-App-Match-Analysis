import type { CSSProperties } from 'react'

type AnyRecord = Record<string, unknown>

function n(value: unknown, fallback = 0): number {
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function s(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  return String(value)
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

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size >= 10 || unitIndex === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`
}

export default function ProcessedStoreProgressPopup({
  visible,
  running,
  event,
  logs,
  message,
  onClose,
  onStop,
  title = 'Processed store progress',
  eyebrow,
}: {
  visible: boolean
  running: boolean
  event: AnyRecord | null
  logs: AnyRecord[]
  message: string
  onClose: () => void
  onStop: () => void
  title?: string
  eyebrow?: string
}) {
  if (!visible) return null

  const percent = Math.max(0, Math.min(100, n(event?.percent)))
  const completedFiles = n(event?.completed_files ?? event?.completed)
  const totalFiles = n(event?.total_files ?? event?.total)
  const completedBytes = n(event?.completed_bytes)
  const totalBytes = n(event?.total_bytes)
  const stage = s(event?.stage, running ? 'building' : 'idle')
  const eta = s(event?.eta_label, running ? 'Calculating' : 'Complete')
  const rows = n(event?.rows)
  const skippedFiles = n(event?.skipped_files)
  const byteLabel = totalBytes > 0 ? `${formatBytes(completedBytes)} of ${formatBytes(totalBytes)}` : 'File count fallback'

  return (
    <aside
      style={{
        position: 'fixed',
        right: 22,
        top: 88,
        zIndex: 80,
        width: 360,
        maxWidth: 'calc(100vw - 44px)',
        border: '1px solid rgba(45,216,233,0.28)',
        background: 'linear-gradient(180deg, rgba(10,15,26,0.98), rgba(15,23,42,0.98))',
        boxShadow: '0 24px 70px rgba(0,0,0,0.45)',
        borderRadius: 20,
        padding: 16,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        <div>
          <div style={{ ...labelStyle(), color: running ? 'var(--accent)' : 'var(--muted)' }}>{eyebrow || (running ? 'Building Parquet' : 'Parquet store')}</div>
          <h3 style={{ margin: '5px 0 0', fontSize: 17, fontWeight: 950 }}>{title}</h3>
        </div>
        <button type="button" onClick={onClose} disabled={running} style={{ ...buttonStyle(false), padding: '6px 9px', opacity: running ? 0.45 : 1 }}>
          Close
        </button>
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: 'var(--muted)', lineHeight: 1.45 }}>
        {message || s(event?.message, 'Preparing processed Parquet build.')}
      </div>

      <div style={{ height: 10, borderRadius: 999, overflow: 'hidden', background: 'rgba(255,255,255,0.08)', marginTop: 14 }}>
        <div style={{ width: `${percent}%`, height: '100%', background: 'linear-gradient(90deg, rgba(45,216,233,0.95), rgba(167,139,250,0.9))' }} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10, marginTop: 14 }}>
        <div style={panelStyle({ padding: 10, borderRadius: 14 })}>
          <div style={labelStyle()}>Progress</div>
          <div style={{ fontSize: 18, fontWeight: 950, marginTop: 4 }}>{percent.toFixed(1)}%</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{byteLabel}</div>
        </div>
        <div style={panelStyle({ padding: 10, borderRadius: 14 })}>
          <div style={labelStyle()}>Estimated ETA</div>
          <div style={{ fontSize: 18, fontWeight: 950, marginTop: 4 }}>{eta}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{stage}</div>
        </div>
        <div style={panelStyle({ padding: 10, borderRadius: 14 })}>
          <div style={labelStyle()}>Rows</div>
          <div style={{ fontSize: 18, fontWeight: 950, marginTop: 4 }}>{rows.toLocaleString()}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>cleaned events</div>
        </div>
        <div style={panelStyle({ padding: 10, borderRadius: 14 })}>
          <div style={labelStyle()}>Files</div>
          <div style={{ fontSize: 18, fontWeight: 950, marginTop: 4 }}>{completedFiles} of {totalFiles || 1}</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3 }}>{skippedFiles} skipped</div>
        </div>
      </div>

      {s(event?.current_file) && (
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--muted)', overflowWrap: 'anywhere', lineHeight: 1.4 }}>
          {s(event?.current_file)}
        </div>
      )}

      <div style={{ display: 'grid', gap: 6, marginTop: 12 }}>
        {logs.slice(0, 4).map((log, index) => (
          <div key={`${s(log.time)}-${index}`} style={{ fontSize: 11, color: 'rgba(226,232,240,0.82)', lineHeight: 1.35 }}>
            <span style={{ color: 'var(--muted)' }}>{s(log.time)}</span> {s(log.message)}
          </div>
        ))}
      </div>

      {running && (
        <button type="button" onClick={onStop} style={{ ...buttonStyle(false), width: '100%', marginTop: 14, borderColor: 'rgba(248,113,113,0.35)', background: 'rgba(248,113,113,0.12)' }}>
          Stop listening
        </button>
      )}
    </aside>
  )
}