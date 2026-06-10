declare module 'react-plotly.js' {
  import type { ComponentType } from 'react'
  const Plot: ComponentType<any>
  export default Plot
}

declare module 'react-plotly.js/factory' {
  export default function createPlotlyComponent(plotly: any): any
}

declare module 'plotly.js/dist/plotly' {
  const Plotly: any
  export default Plotly
}

declare module 'd3-delaunay' {
  export const Delaunay: any
}
