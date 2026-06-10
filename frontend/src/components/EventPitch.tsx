import { useEffect, useId, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import {
  EVENT_GLYPHS,
  PITCH_STANDARD,
  EVENT_KIND_COLOURS,
  TONE_COLOURS,
  eventColour,
  eventRadius,
  eventTooltip,
  laneLabelFromKey,
  normaliseEventKind,
  pitchX,
  pitchY,
  safeBool,
  safeNumber,
  safeText,
  type PitchPoint,
  type PitchTone,
} from '../lib/eventVizSpec'

type AnyRecord = Record<string, unknown>

export type SvgPitchTooltipState = {
  x: number
  y: number
  lines: string[]
} | null

function splitTooltipText(value: string): string[] {
  const text = safeText(value).replace(/\s+/g, ' ').trim()
  if (!text) return []
  const parts = text.split(/(?<=\.)\s+|\s+•\s+/).map((part) => part.trim()).filter(Boolean)
  const lines: string[] = []
  parts.forEach((part) => {
    if (part.length <= 44) {
      lines.push(part)
      return
    }
    const words = part.split(' ')
    let current = ''
    words.forEach((word) => {
      const next = current ? `${current} ${word}` : word
      if (next.length > 44 && current) {
        lines.push(current)
        current = word
      } else {
        current = next
      }
    })
    if (current) lines.push(current)
  })
  return lines.slice(0, 6)
}

export function useSvgPitchTooltip() {
  const [tooltip, setTooltip] = useState<SvgPitchTooltipState>(null)

  const showTooltip = (event: MouseEvent<SVGGElement>, value: string) => {
    const lines = splitTooltipText(value)
    if (!lines.length) {
      setTooltip(null)
      return
    }

    const svg = event.currentTarget.ownerSVGElement
    const matrix = svg?.getScreenCTM()
    if (!svg || !matrix) return

    const point = svg.createSVGPoint()
    point.x = event.clientX
    point.y = event.clientY
    const localPoint = point.matrixTransform(matrix.inverse())
    setTooltip({ x: localPoint.x + 2.6, y: localPoint.y - 2.8, lines })
  }

  const bind = (value: string) => ({
    onMouseEnter: (event: MouseEvent<SVGGElement>) => showTooltip(event, value),
    onMouseMove: (event: MouseEvent<SVGGElement>) => showTooltip(event, value),
    onMouseLeave: () => setTooltip(null),
  })

  return { tooltip, bind, clearTooltip: () => setTooltip(null) }
}

export function SvgPitchTooltip({ tooltip, viewBoxWidth = PITCH_STANDARD.viewBoxWidth, viewBoxHeight = PITCH_STANDARD.viewBoxHeight }: { tooltip: SvgPitchTooltipState; viewBoxWidth?: number; viewBoxHeight?: number }) {
  if (!tooltip || !tooltip.lines.length) return null

  const lineHeight = 4.45
  const width = Math.min(viewBoxWidth - 6, Math.max(30, ...tooltip.lines.map((line) => line.length * 1.62 + 7)))
  const height = tooltip.lines.length * lineHeight + 5.4
  const x = Math.max(2.5, Math.min(viewBoxWidth - width - 2.5, tooltip.x))
  const y = Math.max(2.5, Math.min(viewBoxHeight - height - 2.5, tooltip.y))

  return (
    <g pointerEvents="none" transform={`translate(${x} ${y})`} opacity="0.98">
      <rect width={width} height={height} rx="2.2" fill="rgba(2,6,23,0.96)" stroke="rgba(255,255,255,0.24)" strokeWidth="0.38" />
      {tooltip.lines.map((line, index) => (
        <text key={`${line}-${index}`} x="3.6" y={5.2 + index * lineHeight} fill="rgba(248,250,252,0.96)" fontSize="3.05" fontWeight={index === 0 ? 900 : 750}>
          {line}
        </text>
      ))}
    </g>
  )
}

interface PitchCanvasProps {
  children?: ReactNode
  height?: number
  pad?: number
  showDirection?: boolean
  flip?: boolean
  style?: CSSProperties
}

interface PitchPointLayerProps {
  points: AnyRecord[]
  tone?: PitchTone
  maxPoints?: number
  flip?: boolean
  showLabels?: boolean
}

interface PitchArrowLayerProps {
  arrows: AnyRecord[]
  tone?: PitchTone
  maxArrows?: number
  flip?: boolean
  linkedKey?: string
}

interface PitchHeatLayerProps {
  heatmap: AnyRecord
  tone?: PitchTone
}

interface PitchLaneLayerProps {
  lanes: AnyRecord[]
  tone?: PitchTone
  simple?: boolean
}

interface PitchSequenceLayerProps {
  actions: AnyRecord[]
  tone?: PitchTone
  flip?: boolean
  revealIndex?: number | null
  activeIndex?: number | null
  showBall?: boolean
  playing?: boolean
}

interface EventLegendProps {
  items?: string[]
  tone?: PitchTone
  compact?: boolean
  align?: 'left' | 'right'
  style?: CSSProperties
}

function listFromRecord(record: AnyRecord | undefined, key: string): AnyRecord[] {
  const value = record ? record[key] : undefined
  return Array.isArray(value) ? value as AnyRecord[] : []
}

function panelTextStyle(): CSSProperties {
  return { fill: 'rgba(232,234,240,0.72)', fontSize: 3.2, fontWeight: 800 }
}

function drawMarker(shape: string, x: number, y: number, radius: number, fill: string, stroke: string, strokeWidth: number, opacity: number) {
  if (shape === 'diamond') {
    const points = `${x},${y - radius} ${x + radius},${y} ${x},${y + radius} ${x - radius},${y}`
    return <polygon points={points} fill={fill} opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} />
  }

  if (shape === 'square') {
    return <rect x={x - radius} y={y - radius} width={radius * 2} height={radius * 2} rx={0.8} fill={fill} opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} />
  }

  if (shape === 'triangle') {
    const points = `${x},${y - radius} ${x + radius * 0.92},${y + radius * 0.78} ${x - radius * 0.92},${y + radius * 0.78}`
    return <polygon points={points} fill={fill} opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} />
  }

  if (shape === 'star') {
    const r1 = radius
    const r2 = radius * 0.48
    const points = Array.from({ length: 10 }).map((_, index) => {
      const angle = (-90 + index * 36) * (Math.PI / 180)
      const r = index % 2 === 0 ? r1 : r2
      return `${x + Math.cos(angle) * r},${y + Math.sin(angle) * r}`
    }).join(' ')
    return <polygon points={points} fill={fill} opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} />
  }

  if (shape === 'cross') {
    return (
      <g opacity={opacity} stroke={fill} strokeWidth={strokeWidth + 0.35} strokeLinecap="round">
        <line x1={x - radius} y1={y - radius} x2={x + radius} y2={y + radius} />
        <line x1={x + radius} y1={y - radius} x2={x - radius} y2={y + radius} />
      </g>
    )
  }

  if (shape === 'ring') {
    return <circle cx={x} cy={y} r={radius} fill="rgba(15,23,42,0.72)" opacity={opacity} stroke={fill} strokeWidth={strokeWidth + 0.25} />
  }

  return <circle cx={x} cy={y} r={radius} fill={fill} opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} />
}

function arrowHeadPath(sx: number, sy: number, ex: number, ey: number, size: number) {
  const angle = Math.atan2(ey - sy, ex - sx)
  const left = angle + Math.PI * 0.82
  const right = angle - Math.PI * 0.82
  const p1x = ex + Math.cos(left) * size
  const p1y = ey + Math.sin(left) * size
  const p2x = ex + Math.cos(right) * size
  const p2y = ey + Math.sin(right) * size
  return `M${ex},${ey} L${p1x},${p1y} L${p2x},${p2y} Z`
}


function pitchCoordValue(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return null
  if (numeric < -0.01 || numeric > 100.01) return null
  return Math.max(0, Math.min(100, numeric))
}

function resolveStartCoordinate(record: AnyRecord): { x: number; y: number } | null {
  const x = pitchCoordValue(record.x)
  const y = pitchCoordValue(record.y)
  const startX = pitchCoordValue(record.start_x)
  const startY = pitchCoordValue(record.start_y)

  const hasEventPoint = x !== null && y !== null
  const hasStartPoint = startX !== null && startY !== null
  const startLooksLikeDefaultOrigin = hasStartPoint && startX === 0 && startY === 0 && hasEventPoint && (x !== 0 || y !== 0)

  if (hasStartPoint && !startLooksLikeDefaultOrigin) return { x: startX, y: startY }
  if (hasEventPoint) return { x, y }
  return null
}

function resolveEndCoordinate(record: AnyRecord, start: { x: number; y: number }): { x: number; y: number; explicit: boolean } {
  const endX = pitchCoordValue(record.end_x)
  const endY = pitchCoordValue(record.end_y)
  const endLooksLikeDefaultOrigin = endX === 0 && endY === 0 && (start.x !== 0 || start.y !== 0)

  if (endX !== null && endY !== null && !endLooksLikeDefaultOrigin) {
    return { x: endX, y: endY, explicit: true }
  }

  return { x: start.x, y: start.y, explicit: false }
}

function arrowStrokeStyle(arrow: AnyRecord, tone: PitchTone, linked: boolean) {
  const kind = normaliseEventKind(arrow as PitchPoint)
  const colour = TONE_COLOURS[tone]
  const threatValue = Math.max(0, Math.min(1.4, safeNumber(arrow.threat_value, safeNumber(arrow.threatValue, 0))))

  if (threatValue >= 0.35) {
    return {
      stroke: TONE_COLOURS.amber.primary,
      width: 0.78 + Math.min(0.85, threatValue * 0.72),
      opacity: Math.min(0.94, 0.56 + threatValue * 0.25),
      dash: kind === 'carry' ? '0.95 1.15' : '',
      headSize: 1.35 + Math.min(0.65, threatValue * 0.42),
    }
  }

  if (linked) {
    return { stroke: TONE_COLOURS.amber.primary, width: 1.1, opacity: 0.88, dash: '', headSize: 1.75 }
  }

  if (kind === 'pass') {
    return { stroke: 'rgba(148,163,184,0.56)', width: 0.6, opacity: 0.42, dash: '', headSize: 1.1 }
  }

  if (kind === 'carry') {
    return { stroke: eventColour(arrow as PitchPoint, tone), width: 0.95, opacity: 0.74, dash: '0.95 1.15', headSize: 1.35 }
  }

  if (kind === 'take_on') {
    return { stroke: eventColour(arrow as PitchPoint, tone), width: 1.0, opacity: 0.80, dash: '0.45 1.05', headSize: 1.45 }
  }

  if (kind === 'cross' || kind === 'set_piece') {
    return { stroke: eventColour(arrow as PitchPoint, tone), width: 0.82, opacity: 0.68, dash: '', headSize: 1.55 }
  }

  return { stroke: colour.primary, width: 0.75, opacity: 0.58, dash: '', headSize: 1.35 }
}

export function PitchCanvas({ children, height = 300, pad = 2, showDirection = true, flip = false, style }: PitchCanvasProps) {
  const width = PITCH_STANDARD.viewBoxWidth
  const viewHeight = PITCH_STANDARD.viewBoxHeight
  const reactId = useId()
  const clipId = `pitch-clip-${reactId.replace(/[^a-zA-Z0-9_-]/g, '')}`
  const directionHeight = showDirection ? 22 : 0
  const svgHeight = Math.max(160, height - directionHeight)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === hostRef.current)
    }

    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  const handleFullscreenClick = () => {
    const element = hostRef.current
    if (!element) return

    if (document.fullscreenElement === element) {
      void document.exitFullscreen()
      return
    }

    void element.requestFullscreen()
  }

  return (
    <div
      ref={hostRef}
      style={{
        width: '100%',
        height: isFullscreen ? '100vh' : height,
        display: 'grid',
        gridTemplateRows: showDirection ? '22px minmax(0, 1fr)' : 'minmax(0, 1fr)',
        position: 'relative',
        background: 'rgba(2,6,23,0.96)',
        borderRadius: isFullscreen ? 0 : undefined,
        padding: isFullscreen ? 14 : undefined,
        ...style,
      }}
    >
      <button
        type="button"
        aria-label={isFullscreen ? 'Exit full screen visual' : 'Open full screen visual'}
        title={isFullscreen ? 'Exit full screen' : 'Full screen'}
        onClick={handleFullscreenClick}
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          zIndex: 5,
          minWidth: 0,
          width: 30,
          height: 26,
          padding: 0,
          borderRadius: 9,
          border: '1px solid rgba(255,255,255,0.18)',
          background: 'rgba(2,6,23,0.74)',
          color: 'rgba(232,234,240,0.92)',
          fontSize: 11,
          fontWeight: 950,
          lineHeight: '24px',
          boxShadow: '0 8px 22px rgba(0,0,0,0.28)',
          backdropFilter: 'blur(8px)',
        }}
      >
        {isFullscreen ? '×' : '⛶'}
      </button>

      {showDirection && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: flip ? 'flex-start' : 'flex-end',
            gap: 7,
            padding: '0 4px',
            color: 'rgba(232,234,240,0.68)',
            fontSize: 10,
            fontWeight: 850,
            letterSpacing: 0.2,
            textTransform: 'uppercase',
          }}
        >
          {flip && <span style={{ fontSize: 15, lineHeight: 1 }}>←</span>}
          <span>attacking direction</span>
          {!flip && <span style={{ fontSize: 15, lineHeight: 1 }}>→</span>}
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${viewHeight}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: '100%', height: isFullscreen ? '100%' : svgHeight, display: 'block' }}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={pad} y={pad} width={width - pad * 2} height={viewHeight - pad * 2} rx="2.8" />
          </clipPath>
        </defs>
        <rect x={pad} y={pad} width={width - pad * 2} height={viewHeight - pad * 2} rx="2.8" fill="rgba(15,23,42,0.95)" stroke="rgba(255,255,255,0.22)" strokeWidth="0.75" />
        <line x1={width / 2} x2={width / 2} y1={pad} y2={viewHeight - pad} stroke="rgba(255,255,255,0.14)" strokeWidth="0.55" />
        <circle cx={width / 2} cy={viewHeight / 2} r="9.15" fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="0.55" />
        <circle cx={width / 2} cy={viewHeight / 2} r="0.75" fill="rgba(255,255,255,0.28)" />
        <rect x={pad} y="13.84" width="16.5" height="40.32" fill="none" stroke="rgba(255,255,255,0.14)" strokeWidth="0.55" />
        <rect x={width - pad - 16.5} y="13.84" width="16.5" height="40.32" fill="none" stroke="rgba(255,255,255,0.14)" strokeWidth="0.55" />
        <rect x={pad} y="24.84" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.13)" strokeWidth="0.5" />
        <rect x={width - pad - 5.5} y="24.84" width="5.5" height="18.32" fill="none" stroke="rgba(255,255,255,0.13)" strokeWidth="0.5" />
        <circle cx="11" cy="34" r="0.75" fill="rgba(255,255,255,0.25)" />
        <circle cx="94" cy="34" r="0.75" fill="rgba(255,255,255,0.25)" />
        <g clipPath={`url(#${clipId})`}>{children}</g>
      </svg>
    </div>
  )
}

export function PitchLaneLayer({ lanes, tone = 'cyan' }: PitchLaneLayerProps) {
  const colour = TONE_COLOURS[tone]
  const pitchTooltip = useSvgPitchTooltip()
  return (
    <g>
      {lanes.map((lane, index) => {
        const key = safeText(lane.lane, safeText(lane.key, `lane_${index}`))
        const yMin = safeNumber(lane.y_min, safeNumber(lane.yMin, key === 'left' ? 0 : key === 'central' ? 33.333 : 66.667))
        const yMax = safeNumber(lane.y_max, safeNumber(lane.yMax, key === 'left' ? 33.333 : key === 'central' ? 66.667 : 100))
        const share = Math.max(0, Math.min(100, safeNumber(lane.share_pct, safeNumber(lane.share, 0))))
        const yStart = pitchY(yMin)
        const yEnd = pitchY(yMax)
        const y = Math.min(yStart, yEnd)
        const h = Math.abs(yEnd - yStart)
        const opacity = 0.05 + (share / 100) * 0.38
        const tooltip = `${safeText(lane.label, laneLabelFromKey(key))}. Share ${share.toFixed(1)}%. Actions ${safeNumber(lane.count)}. Final third entries ${safeNumber(lane.final_third_entries)}. Box entries ${safeNumber(lane.box_entries)}. Shots ${safeNumber(lane.shots)}.`
        return (
          <g key={`${key}-${index}`} {...pitchTooltip.bind(tooltip)}>
            <rect x="2" y={y} width={PITCH_STANDARD.viewBoxWidth - 4} height={Math.max(0, h)} fill={colour.primary} opacity={opacity} />
            <text x="5" y={y + Math.max(5.5, h / 2 + 1.5)} fill="rgba(232,234,240,0.90)" fontSize="3.2" fontWeight="900">
              {safeText(lane.label, laneLabelFromKey(key))} {share.toFixed(1)}%
            </text>
            <title>{`${safeText(lane.label, laneLabelFromKey(key))}. Share ${share.toFixed(1)}%. Actions ${safeNumber(lane.count)}. Final third entries ${safeNumber(lane.final_third_entries)}. Box entries ${safeNumber(lane.box_entries)}. Shots ${safeNumber(lane.shots)}.`}</title>
          </g>
        )
      })}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

export function PitchArrowLayer({ arrows, tone = 'cyan', maxArrows = 180, flip = false, linkedKey = 'led_to_shot' }: PitchArrowLayerProps) {
  const pitchTooltip = useSvgPitchTooltip()
  return (
    <g>
      {arrows.slice(0, maxArrows).map((arrow, index) => {
        const eventPoint = { ...arrow, type: arrow.event_type ?? arrow.type } as PitchPoint
        const kind = normaliseEventKind(eventPoint)
        const start = resolveStartCoordinate(arrow)
        if (!start) return null

        const end = resolveEndCoordinate(arrow, start)
        const sx = pitchX(start.x, flip)
        const sy = pitchY(start.y, flip)
        const ex = pitchX(end.x, flip)
        const ey = pitchY(end.y, flip)
        const linked = safeBool(arrow[linkedKey]) || safeBool(arrow.led_to_shot) || safeBool(arrow.led_to_goal)
        const style = arrowStrokeStyle(eventPoint as AnyRecord, tone, linked)
        const isVeryShort = Math.hypot(ex - sx, ey - sy) < 1.4
        const isTakeOn = kind === 'take_on'
        const isCarry = kind === 'carry'
        const endpointStroke = linked ? 'rgba(255,255,255,0.82)' : 'rgba(255,255,255,0.58)'
        const shouldDrawPath = end.explicit && !isVeryShort
        const shouldDrawEnd = end.explicit || (!isTakeOn && !isCarry)

        const tooltip = `${safeText(arrow.minute)}' ${safeText(arrow.player, 'Unknown')} ${safeText(arrow.event_type, safeText(arrow.type, 'Event'))}${isTakeOn ? ' start to end' : ''}${isCarry ? ' carry path' : ''}${linked ? ' led to value' : ''}`

        return (
          <g key={`${safeText(arrow.event_index, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
            {shouldDrawPath && (
              <line
                x1={sx}
                y1={sy}
                x2={ex}
                y2={ey}
                stroke={style.stroke}
                strokeWidth={style.width}
                strokeLinecap="round"
                opacity={style.opacity}
                strokeDasharray={style.dash}
              />
            )}

            {shouldDrawPath && !isTakeOn && (
              <path
                d={arrowHeadPath(sx, sy, ex, ey, style.headSize)}
                fill={style.stroke}
                opacity={style.opacity}
              />
            )}

            {isTakeOn ? (
              <>
                <circle
                  cx={sx}
                  cy={sy}
                  r="1.05"
                  fill="rgba(15,23,42,0.92)"
                  stroke={style.stroke}
                  strokeWidth="0.5"
                  opacity="0.94"
                />
                {end.explicit && drawMarker('diamond', ex, ey, linked ? 1.7 : 1.4, style.stroke, endpointStroke, 0.56, linked ? 0.96 : 0.86)}
              </>
            ) : isCarry ? (
              <>
                <circle
                  cx={sx}
                  cy={sy}
                  r="0.58"
                  fill="rgba(15,23,42,0.82)"
                  stroke={style.stroke}
                  strokeWidth="0.38"
                  opacity="0.72"
                />
                {end.explicit && (
                  <circle
                    cx={ex}
                    cy={ey}
                    r={linked ? 1.05 : 0.78}
                    fill={style.stroke}
                    opacity={linked ? 0.88 : 0.66}
                    stroke="rgba(255,255,255,0.50)"
                    strokeWidth="0.28"
                  />
                )}
              </>
            ) : shouldDrawEnd ? (
              <circle
                cx={ex}
                cy={ey}
                r={linked ? 0.95 : 0.65}
                fill={style.stroke}
                opacity={linked ? 0.86 : 0.64}
                stroke="rgba(255,255,255,0.48)"
                strokeWidth="0.24"
              />
            ) : null}

            <title>
              {`${safeText(arrow.minute)}' ${safeText(arrow.player, 'Unknown')} ${safeText(arrow.event_type, safeText(arrow.type, 'Event'))}${isTakeOn ? ' start to end' : ''}${isCarry ? ' carry path' : ''}${linked ? ' led to value' : ''}`}
            </title>
          </g>
        )
      })}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

export function PitchPointLayer({ points, tone = 'cyan', maxPoints = 300, flip = false, showLabels = false }: PitchPointLayerProps) {
  const pitchTooltip = useSvgPitchTooltip()
  const sorted = [...points].slice(0, maxPoints).sort((a, b) => {
    const aKind = normaliseEventKind(a as PitchPoint)
    const bKind = normaliseEventKind(b as PitchPoint)
    return EVENT_GLYPHS[aKind].drawPriority - EVENT_GLYPHS[bKind].drawPriority
  })

  return (
    <g>
      {sorted.map((point, index) => {
        const eventPoint = point as PitchPoint
        const pointX = pitchCoordValue(eventPoint.x)
        const pointY = pitchCoordValue(eventPoint.y)
        if (pointX === null || pointY === null) return null
        const x = pitchX(pointX, flip)
        const y = pitchY(pointY, flip)
        const kind = normaliseEventKind(eventPoint)
        const spec = EVENT_GLYPHS[kind]
        const fill = eventColour(eventPoint, tone)
        const radius = eventRadius(eventPoint)
        const stroke = kind === 'goal' ? 'rgba(255,255,255,0.92)' : kind === 'pass' || kind === 'touch' || kind === 'other' ? 'rgba(255,255,255,0.26)' : 'rgba(255,255,255,0.58)'
        const tooltip = eventTooltip(eventPoint)
        return (
          <g key={`${safeText(point.event_index, String(index))}-${index}`} {...pitchTooltip.bind(tooltip)}>
            {drawMarker(spec.shape, x, y, radius, fill, stroke, spec.strokeWidth, spec.opacity)}
            {showLabels && (
              <text x={x + radius + 1.5} y={y - radius - 0.6} fill="rgba(232,234,240,0.78)" fontSize="3" fontWeight="900">
                {safeText(point.order)}
              </text>
            )}
            <title>{eventTooltip(eventPoint)}</title>
          </g>
        )
      })}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

export function PitchSequenceLayer({ actions, tone = 'cyan', flip = false, revealIndex = null, activeIndex = null, showBall = false, playing = false }: PitchSequenceLayerProps) {
  const pitchTooltip = useSvgPitchTooltip()
  const ordered = [...actions].sort((a, b) => safeNumber(a.order, 0) - safeNumber(b.order, 0))
  const revealLimit = revealIndex === null || revealIndex === undefined ? ordered.length - 1 : Math.max(0, Math.min(ordered.length - 1, revealIndex))
  const visibleActions = ordered.filter((_, index) => index <= revealLimit)
  const activeLimit = activeIndex === null || activeIndex === undefined ? revealLimit : Math.max(0, Math.min(ordered.length - 1, activeIndex))
  const activeAction = ordered[activeLimit]

  const pointFor = (action: AnyRecord) => {
    const point = action as PitchPoint
    const hasEnd = action.end_x !== null && action.end_y !== null && action.end_x !== undefined && action.end_y !== undefined
    const sx = pitchX(point.x, flip)
    const sy = pitchY(point.y, flip)
    const ex = pitchX(point.end_x ?? point.x, flip)
    const ey = pitchY(point.end_y ?? point.y, flip)
    return { sx, sy, ex, ey, hasEnd }
  }

  const ballPoint = activeAction ? pointFor(activeAction) : null
  const ballX = ballPoint ? (ballPoint.hasEnd ? ballPoint.ex : ballPoint.sx) : 0
  const ballY = ballPoint ? (ballPoint.hasEnd ? ballPoint.ey : ballPoint.sy) : 0
  const ballTooltip = activeAction ? `${safeText(activeAction.minute)}' ${safeText(activeAction.player, 'Unknown')} ${safeText(activeAction.type, safeText(activeAction.event_type, 'Action'))}` : ''

  return (
    <g>
      {visibleActions.map((action, index) => {
        if (index >= visibleActions.length - 1) return null
        const current = pointFor(action)
        const next = pointFor(visibleActions[index + 1])
        const linked = Math.hypot(next.sx - current.ex, next.sy - current.ey) > 1.2
        if (!linked) return null
        const tooltip = `Sequence link from action ${safeText(action.order, String(index + 1))} to ${safeText(visibleActions[index + 1]?.order, String(index + 2))}`
        return (
          <g key={`connector-${safeText(action.event_index, String(index))}-${index}`} opacity="0.56" {...pitchTooltip.bind(tooltip)}>
            <line
              x1={current.ex}
              y1={current.ey}
              x2={next.sx}
              y2={next.sy}
              stroke="rgba(232,234,240,0.58)"
              strokeWidth="0.72"
              strokeLinecap="round"
              strokeDasharray="1.4 1.15"
            />
            <path d={arrowHeadPath(current.ex, current.ey, next.sx, next.sy, 1.25)} fill="rgba(232,234,240,0.58)" />
            <title>{`Sequence link from action ${safeText(action.order, String(index + 1))} to ${safeText(visibleActions[index + 1]?.order, String(index + 2))}`}</title>
          </g>
        )
      })}

      {visibleActions.map((action, index) => {
        const point = action as PitchPoint
        const { sx, sy, ex, ey, hasEnd } = pointFor(action)
        const kind = normaliseEventKind(point)
        const fill = eventColour(point, tone)
        const isFinalAction = index === ordered.length - 1
        const isActiveAction = index === activeLimit && showBall
        const isShot = kind === 'shot' || kind === 'goal' || safeBool(point.is_shot)
        const radius = eventRadius(point) + (isFinalAction || isShot ? 0.55 : 0) + (isActiveAction ? 0.35 : 0)
        const spec = EVENT_GLYPHS[kind]
        const actionXt = Math.max(0, safeNumber(action.positive_xt, safeNumber(action.xt_added)))
        const lineWidthValue = (kind === 'pass' ? 0.72 : kind === 'carry' || kind === 'take_on' ? 1.1 : 0.95) + Math.min(1.25, actionXt * 9) + (isActiveAction ? 0.28 : 0)
        const lineWidth = String(lineWidthValue)
        const lineOpacity = Math.min(0.92, (kind === 'pass' ? 0.50 : 0.78) + Math.min(0.18, actionXt * 1.4) + (isActiveAction ? 0.12 : 0))
        const dash = kind === 'carry' ? '0.95 1.15' : kind === 'take_on' ? '0.45 1.05' : ''
        const tooltip = eventTooltip(point) + (safeNumber(action.xg, -1) >= 0 ? ' xG ' + safeNumber(action.xg).toFixed(2) : '') + (actionXt > 0 ? ' xT ' + actionXt.toFixed(3) : '')
        return (
          <g key={`${safeText(action.event_index, String(index))}-${safeText(action.order, String(index))}`} {...pitchTooltip.bind(tooltip)}>
            {hasEnd && (
              <>
                <line
                  x1={sx}
                  y1={sy}
                  x2={ex}
                  y2={ey}
                  stroke={eventColour(point, tone)}
                  strokeWidth={lineWidth}
                  strokeLinecap="round"
                  opacity={lineOpacity}
                  strokeDasharray={dash}
                />
                {Math.hypot(ex - sx, ey - sy) > 1.3 && <path d={arrowHeadPath(sx, sy, ex, ey, isShot ? 1.75 : 1.35)} fill={eventColour(point, tone)} opacity={lineOpacity} />}
              </>
            )}
            {kind === 'goal' || safeBool(point.is_goal) ? (
              <text x={sx} y={sy + 1.75} fontSize="5.4" fontWeight="950" textAnchor="middle">⚽</text>
            ) : (
              <>
                {(isFinalAction || isActiveAction) && <circle cx={sx} cy={sy} r={radius + 1.4} fill="none" stroke={isActiveAction ? 'rgba(255,255,255,0.62)' : 'rgba(255,255,255,0.38)'} strokeWidth="0.65" />}
                {drawMarker(spec.shape, sx, sy, radius, fill, 'rgba(255,255,255,0.78)', spec.strokeWidth, spec.opacity)}
                <circle cx={sx} cy={sy} r="2.15" fill="rgba(2,6,23,0.92)" stroke="rgba(255,255,255,0.55)" strokeWidth="0.38" />
                <text x={sx} y={sy + 0.92} fill="rgba(232,234,240,0.96)" fontSize="2.45" fontWeight="950" textAnchor="middle">{safeText(action.order, String(index + 1))}</text>
              </>
            )}
            <title>{eventTooltip(point) + (safeNumber(action.xg, -1) >= 0 ? ' xG ' + safeNumber(action.xg).toFixed(2) : '') + (actionXt > 0 ? ' xT ' + actionXt.toFixed(3) : '')}</title>
          </g>
        )
      })}

      {showBall && ballPoint && (
        <g transform={`translate(${ballX} ${ballY})`} style={{ transition: 'transform 420ms ease' }} {...pitchTooltip.bind(`Ball position. ${ballTooltip}`)}>
          <circle r={playing ? 3.05 : 2.65} fill="rgba(250,204,21,0.96)" stroke="rgba(15,23,42,0.92)" strokeWidth="0.55" />
          <circle r={playing ? 5.1 : 4.3} fill="none" stroke="rgba(250,204,21,0.38)" strokeWidth="0.72" />
          <title>{`Ball position. ${ballTooltip}`}</title>
        </g>
      )}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

export function PitchHeatLayer({ heatmap, tone = 'amber' }: PitchHeatLayerProps) {
  const pitchTooltip = useSvgPitchTooltip()
  const cells = listFromRecord(heatmap, 'cells')
  const xBins = Math.max(1, safeNumber(heatmap.x_bins, 6))
  const yBins = Math.max(1, safeNumber(heatmap.y_bins, 5))
  const maxCount = Math.max(1, ...cells.map((cell) => Math.max(safeNumber(cell.value), safeNumber(cell.count))))
  const cellWidth = (PITCH_STANDARD.viewBoxWidth - 4) / xBins
  const cellHeight = (PITCH_STANDARD.viewBoxHeight - 4) / yBins
  const colour = TONE_COLOURS[tone]

  return (
    <g>
      {cells.map((cell, index) => {
        const xBin = safeNumber(cell.x_bin)
        const yBin = safeNumber(cell.y_bin)
        const x = 2 + xBin * cellWidth
        const y = 2 + (yBins - 1 - yBin) * cellHeight
        const cellValue = Math.max(safeNumber(cell.value), safeNumber(cell.count))
        const opacity = 0.12 + (cellValue / maxCount) * 0.62
        const tooltip = `Heat cell ${xBin + 1}, ${yBin + 1}. Count ${safeNumber(cell.count)}. Value ${safeNumber(cell.value).toFixed(2)}.`
        return (
          <g key={`${safeNumber(cell.x_bin)}-${safeNumber(cell.y_bin)}-${index}`} {...pitchTooltip.bind(tooltip)}>
            <rect x={x} y={y} width={cellWidth} height={cellHeight} fill={colour.primary} opacity={opacity} stroke="rgba(255,255,255,0.08)" strokeWidth="0.35" />
            <text x={x + cellWidth / 2} y={y + cellHeight / 2 + 1.4} textAnchor="middle" fill="rgba(255,255,255,0.88)" fontSize="3.5" fontWeight="900">{safeNumber(cell.value) > 0 && safeNumber(cell.value) !== safeNumber(cell.count) ? safeNumber(cell.value).toFixed(2) : safeNumber(cell.count)}</text>
            <title>{`Heat cell ${xBin + 1}, ${yBin + 1}. Count ${safeNumber(cell.count)}. Value ${safeNumber(cell.value).toFixed(2)}.`}</title>
          </g>
        )
      })}
      <SvgPitchTooltip tooltip={pitchTooltip.tooltip} />
    </g>
  )
}

export function EventLegend({ items = ['pass', 'cross', 'carry', 'take_on', 'shot', 'goal', 'tackle', 'interception', 'recovery'], tone = 'cyan' }: EventLegendProps) {
  const customItems: Record<string, { label: string; shape: string; colour: string; stroke?: string }> = {
    take_on_path: { label: 'Take on path', shape: 'dotted_line', colour: EVENT_KIND_COLOURS.take_on },
    take_on_start: { label: 'Take on start', shape: 'circle', colour: 'rgba(15,23,42,0.90)', stroke: EVENT_KIND_COLOURS.take_on },
    take_on_end: { label: 'Take on end', shape: 'diamond', colour: EVENT_KIND_COLOURS.take_on, stroke: 'rgba(255,255,255,0.62)' },
    carry_path: { label: 'Carry path', shape: 'solid_line', colour: EVENT_KIND_COLOURS.carry },
    carry_end: { label: 'Carry end', shape: 'circle', colour: EVENT_KIND_COLOURS.carry, stroke: 'rgba(255,255,255,0.58)' },
    heat_funnel: { label: 'Heat funnel', shape: 'square', colour: TONE_COLOURS[tone].soft, stroke: 'rgba(255,255,255,0.32)' },
  }

  return (
    <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-end', marginTop: 6 }}>
      {items.map((item) => {
        const custom = customItems[item]

        if (custom) {
          return (
            <span key={item} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--muted)' }}>
              <svg viewBox="0 0 24 14" style={{ width: custom.shape.includes('line') ? 22 : 12, height: 12 }}>
                {custom.shape === 'dotted_line' ? (
                  <line x1="2" y1="7" x2="22" y2="7" stroke={custom.colour} strokeWidth="2" strokeLinecap="round" strokeDasharray="1 3" />
                ) : custom.shape === 'solid_line' ? (
                  <line x1="2" y1="7" x2="22" y2="7" stroke={custom.colour} strokeWidth="2" strokeLinecap="round" />
                ) : (
                  drawMarker(custom.shape, 7, 7, 3.1, custom.colour, custom.stroke ?? 'rgba(255,255,255,0.58)', 0.62, 0.88)
                )}
              </svg>
              {custom.label}
            </span>
          )
        }

        const fake = { type: item, event_kind: item, is_goal: item === 'goal', is_shot: item === 'shot' || item === 'goal' }
        const kind = normaliseEventKind(fake)
        const spec = EVENT_GLYPHS[kind]

        return (
          <span key={item} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: 'var(--muted)' }}>
            <svg viewBox="0 0 14 14" style={{ width: 12, height: 12 }}>
              {drawMarker(spec.shape, 7, 7, Math.min(3.4, spec.radius + 0.55), eventColour(fake, tone), 'rgba(255,255,255,0.58)', spec.strokeWidth, spec.opacity)}
            </svg>
            {spec.label}
          </span>
        )
      })}
    </div>
  )
}

export function EmptyPitchNote({ label = 'No matching events to draw.' }: { label?: string }) {
  return <text x={PITCH_STANDARD.viewBoxWidth / 2} y={PITCH_STANDARD.viewBoxHeight / 2} textAnchor="middle" fill="rgba(232,234,240,0.58)" fontSize="4" fontWeight="850">{label}</text>
}