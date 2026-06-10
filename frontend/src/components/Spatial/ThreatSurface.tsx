import { memo, useMemo } from 'react'
import Plot from '../../lib/PlotlyChart'
import type { ExpectedThreatSurfaceResponse } from '../../types/api'

interface ThreatSurfaceProps {
  surface: ExpectedThreatSurfaceResponse
  height?: number
}

function ThreatSurfaceComponent({ surface, height = 420 }: ThreatSurfaceProps) {
  const z = useMemo(() => surface.xt_grid, [surface.xt_grid])
  const x = useMemo(() => surface.x_centres, [surface.x_centres])
  const y = useMemo(() => surface.y_centres, [surface.y_centres])

  return (
    <div
      style={{
        width: '100%',
        height,
        borderRadius: 14,
        overflow: 'hidden',
        border: '1px solid rgba(255,255,255,0.08)',
        background: 'rgba(12,18,32,0.96)',
      }}
    >
      <Plot
        data={[
          {
            type: 'surface',
            z,
            x,
            y,
            hovertemplate: 'x: %{x:.1f}m<br/>y: %{y:.1f}m<br/>xT: %{z:.4f}<extra></extra>',
            contours: {
              z: {
                show: true,
                usecolormap: true,
                highlightwidth: 1,
                project: { z: true },
              },
            },
          },
        ]}
        layout={{
          autosize: true,
          paper_bgcolor: 'rgba(0,0,0,0)',
          plot_bgcolor: 'rgba(0,0,0,0)',
          margin: { l: 0, r: 0, t: 10, b: 0 },
          scene: {
            xaxis: { title: 'Pitch length', range: [0, 120], showspikes: false },
            yaxis: { title: 'Pitch width', range: [0, 80], showspikes: false },
            zaxis: { title: 'xT', rangemode: 'tozero', showspikes: false },
            camera: {
              eye: { x: 1.6, y: 1.35, z: 0.9 },
            },
            aspectratio: { x: 1.5, y: 1, z: 0.45 },
          },
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%', height: '100%' }}
        useResizeHandler
      />
    </div>
  )
}

export const ThreatSurface = memo(ThreatSurfaceComponent)