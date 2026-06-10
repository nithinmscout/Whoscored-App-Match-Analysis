import { memo, useMemo } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { Delaunay } from 'd3-delaunay'
import * as THREE from 'three'
import type { PitchControlPlayerPoint } from '../../types/api'

interface PitchControlSceneProps {
  players: PitchControlPlayerPoint[]
  height?: number
}

type AuraCell = {
  key: string
  polygon: [number, number][]
  colour: string
  height: number
  label: string
}

const HOME_COLOUR = '#29b6f6'
const AWAY_COLOUR = '#ef5350'

function toWorld(polygon: [number, number][]): [number, number][] {
  return polygon.map(([x, y]) => [x - 60, y - 40])
}

function buildAuraCells(players: PitchControlPlayerPoint[]): AuraCell[] {
  if (players.length < 2) return []
  const delaunay = Delaunay.from(players, (player) => player.x, (player) => player.y)
  const voronoi = delaunay.voronoi([0, 0, 120, 80])

  const cells: AuraCell[] = []
  players.forEach((player, index) => {
    const raw = voronoi.cellPolygon(index)
    if (!raw) return
    const polygon = Array.from(raw)
      .map(([x, y]) => [Number(x), Number(y)] as [number, number])
      .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))

    if (polygon.length < 3) return

    cells.push({
      key: `${player.team}-${player.player}-${index}`,
      polygon: toWorld(polygon),
      colour: player.team_side === 'home' ? HOME_COLOUR : AWAY_COLOUR,
      height: Math.max(1.25, 2 + (player.local_xt * 60)),
      label: `${player.player} · ${player.local_xt.toFixed(3)} xT`,
    })
  })

  return cells
}

function AuraMesh({ cell }: { cell: AuraCell }) {
  const geometry = useMemo(() => {
    const shape = new THREE.Shape()
    cell.polygon.forEach(([x, y], index) => {
      if (index === 0) shape.moveTo(x, y)
      else shape.lineTo(x, y)
    })
    shape.closePath()

    const geom = new THREE.ExtrudeGeometry(shape, {
      depth: cell.height,
      bevelEnabled: false,
      steps: 1,
    })
    geom.rotateX(-Math.PI / 2)
    geom.computeVertexNormals()
    return geom
  }, [cell])

  return (
    <mesh geometry={geometry} position={[0, 0, 0]}>
      <meshStandardMaterial color={cell.colour} transparent opacity={0.62} />
    </mesh>
  )
}

function PitchLines() {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.02, 0]}>
        <planeGeometry args={[120, 80]} />
        <meshStandardMaterial color="#102331" />
      </mesh>
      <lineSegments>
        <edgesGeometry args={[new THREE.PlaneGeometry(120, 80)]} />
        <lineBasicMaterial color="#d9f3ff" />
      </lineSegments>
      <group position={[0, 0.02, 0]}>
        <line>
          <bufferGeometry>
            <bufferAttribute
              attach="attributes-position"
              args={[new Float32Array([0, 0, -40, 0, 0, 40]), 3]}
            />
          </bufferGeometry>
          <lineBasicMaterial color="#d9f3ff" />
        </line>
      </group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
        <ringGeometry args={[9.1, 9.15, 64]} />
        <meshBasicMaterial color="#d9f3ff" side={THREE.DoubleSide} />
      </mesh>
    </group>
  )
}

function PitchControlSceneComponent({ players, height = 460 }: PitchControlSceneProps) {
  const auraCells = useMemo(() => buildAuraCells(players), [players])

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
      <Canvas camera={{ position: [0, 95, 105], fov: 42 }}>
        <ambientLight intensity={0.55} />
        <directionalLight position={[30, 80, 20]} intensity={1.2} />
        <PitchLines />
        {auraCells.map((cell) => (
          <AuraMesh key={cell.key} cell={cell} />
        ))}
        <OrbitControls enablePan={false} maxPolarAngle={Math.PI / 2.2} minDistance={75} maxDistance={190} />
      </Canvas>
    </div>
  )
}

export const PitchControlScene = memo(PitchControlSceneComponent)
