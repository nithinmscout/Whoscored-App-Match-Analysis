import { memo, useMemo } from 'react'
import DeckGL from '@deck.gl/react'
import { ArcLayer } from '@deck.gl/layers'
import { COORDINATE_SYSTEM, OrthographicView } from '@deck.gl/core'
import type { XTArcPass } from '../../types/api'

interface PassNetwork3DProps {
  passes: XTArcPass[]
  width?: number | string
  height?: number
}

function sortPassesForRendering(passes: XTArcPass[]): XTArcPass[] {
  return [...passes].sort((a, b) => a.xt_added - b.xt_added)
}

function PassNetwork3DComponent({
  passes,
  width = '100%',
  height = 420,
}: PassNetwork3DProps) {
  const data = useMemo(() => sortPassesForRendering(passes).slice(-250), [passes])

  const layers = useMemo(() => {
    return [
      new ArcLayer<XTArcPass>({
        id: 'pass-arcs-3d',
        data,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getSourcePosition: (d) => d.start,
        getTargetPosition: (d) => d.end,
        getSourceColor: (d) => (d.pass_type === 'high' ? [255, 180, 60, 220] : [80, 200, 255, 180]),
        getTargetColor: (d) => (d.pass_type === 'high' ? [255, 120, 40, 240] : [30, 255, 170, 220]),
        getWidth: (d) => Math.max(1, Math.min(8, 1 + (d.xt_added * 40))),
        pickable: true,
        autoHighlight: true,
        greatCircle: false,
        getHeight: (d) => d.arc_height,
        getTilt: () => 0,
        updateTriggers: {
          getSourceColor: data,
          getTargetColor: data,
          getWidth: data,
          getHeight: data,
        },
      } as any),
    ]
  }, [data])

  const tooltip = ({ object }: { object?: XTArcPass }) => {
    if (!object) return null
    return {
      html: `<div style="font-size:12px"><strong>${object.player}</strong><br/>${object.team}<br/>xT added: ${object.xt_added.toFixed(3)}<br/>Type: ${object.pass_type}</div>`,
      style: {
        backgroundColor: 'rgba(12, 18, 32, 0.95)',
        color: '#fff',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '8px',
      },
    }
  }

  return (
    <div
      style={{
        width,
        height,
        position: 'relative',
        borderRadius: 14,
        overflow: 'hidden',
        background:
          'linear-gradient(180deg, rgba(16,22,38,0.96) 0%, rgba(7,12,21,1) 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <DeckGL
        views={new OrthographicView({ id: 'pitch-view' })}
        controller={true}
        initialViewState={{
          target: [60, 40, 0],
          zoom: 3.1,
          minZoom: 1.8,
          maxZoom: 8,
        }}
        layers={layers as any}
        getTooltip={tooltip as any}
      >
        <div
          style={{
            position: 'absolute',
            inset: 12,
            border: '2px solid rgba(255,255,255,0.18)',
            borderRadius: 10,
            pointerEvents: 'none',
          }}
        />
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: 12,
            bottom: 12,
            width: 1,
            transform: 'translateX(-0.5px)',
            background: 'rgba(255,255,255,0.12)',
            pointerEvents: 'none',
          }}
        />
      </DeckGL>
    </div>
  )
}

export const PassNetwork3D = memo(PassNetwork3DComponent)