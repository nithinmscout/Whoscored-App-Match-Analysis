import { memo, useEffect, useRef, type CSSProperties } from 'react'
import * as PlotlyModule from 'plotly.js/dist/plotly'

type PlotlyData = Record<string, unknown>
type PlotlyLayout = Record<string, unknown>
type PlotlyConfig = Record<string, unknown>

type PlotlyApi = {
  react?: (element: HTMLElement, data: PlotlyData[], layout?: PlotlyLayout, config?: PlotlyConfig) => Promise<unknown> | unknown
  newPlot?: (element: HTMLElement, data: PlotlyData[], layout?: PlotlyLayout, config?: PlotlyConfig) => Promise<unknown> | unknown
  purge?: (element: HTMLElement) => void
  Plots?: {
    resize?: (element: HTMLElement) => void
  }
}

type PlotlyChartProps = {
  data: PlotlyData[]
  layout?: PlotlyLayout
  config?: PlotlyConfig
  style?: CSSProperties
  className?: string
  useResizeHandler?: boolean
}

const Plotly = ((PlotlyModule as { default?: PlotlyApi }).default ?? PlotlyModule) as PlotlyApi

function PlotlyChartComponent({
  data,
  layout,
  config,
  style,
  className,
  useResizeHandler = false,
}: PlotlyChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const safeData = Array.isArray(data) ? data : []
    const safeLayout: PlotlyLayout = {
      ...(layout ?? {}),
      autosize: layout?.autosize ?? true,
    }

    if (typeof Plotly.react === 'function') {
      void Plotly.react(container, safeData, safeLayout, config)
      return
    }

    if (typeof Plotly.newPlot === 'function') {
      void Plotly.newPlot(container, safeData, safeLayout, config)
    }
  }, [config, data, layout])

  useEffect(() => {
    if (!useResizeHandler) return undefined

    const container = containerRef.current
    if (!container) return undefined

    const resize = () => {
      if (typeof Plotly.Plots?.resize === 'function') {
        Plotly.Plots.resize(container)
      }
    }

    resize()
    window.addEventListener('resize', resize)

    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(container)
    }

    return () => {
      window.removeEventListener('resize', resize)
      observer?.disconnect()
    }
  }, [useResizeHandler])

  useEffect(() => {
    const container = containerRef.current
    return () => {
      if (container && typeof Plotly.purge === 'function') {
        Plotly.purge(container)
      }
    }
  }, [])

  return <div ref={containerRef} className={className} style={style} />
}

const PlotlyChart = memo(PlotlyChartComponent)

export default PlotlyChart
