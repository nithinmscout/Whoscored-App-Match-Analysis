import { memo, useCallback, useMemo, useState, type CSSProperties, type UIEvent } from 'react'
import type { TableRow } from '../types/api'

interface Props {
  columns: string[]
  rows: TableRow[]
  maxRows?: number
  height?: number
}

const HEADER_HEIGHT = 38
const ROW_HEIGHT = 34
const OVERSCAN = 10
const MIN_COLUMN_WIDTH = 140

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

const DataRow = memo(function DataRow({
  row,
  columns,
  gridTemplateColumns,
  top,
}: {
  row: TableRow
  columns: string[]
  gridTemplateColumns: string
  top: number
}) {
  return (
    <div
      role="row"
      style={{
        position: 'absolute',
        top,
        left: 0,
        right: 0,
        height: ROW_HEIGHT,
        display: 'grid',
        gridTemplateColumns,
        alignItems: 'center',
        borderBottom: '1px solid var(--border)',
        background: 'var(--panel)',
      }}
    >
      {columns.map(column => (
        <div
          key={column}
          role="cell"
          title={formatCellValue(row[column])}
          style={{
            minWidth: 0,
            padding: '0 10px',
            fontSize: 12,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {formatCellValue(row[column])}
        </div>
      ))}
    </div>
  )
})

export default function DataTable({ columns, rows, maxRows = 300, height = 480 }: Props) {
  const [scrollTop, setScrollTop] = useState(0)

  const cappedRows = useMemo(() => rows.slice(0, maxRows), [rows, maxRows])
  const bodyHeight = Math.max(height - HEADER_HEIGHT, 140)
  const gridTemplateColumns = useMemo(
    () => columns.map(() => `minmax(${MIN_COLUMN_WIDTH}px, 1fr)`).join(' '),
    [columns],
  )
  const contentMinWidth = Math.max(columns.length * MIN_COLUMN_WIDTH, 560)

  const visibleCount = Math.ceil(bodyHeight / ROW_HEIGHT) + OVERSCAN * 2
  const startIndex = Math.max(Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN, 0)
  const endIndex = Math.min(cappedRows.length, startIndex + visibleCount)
  const visibleRows = cappedRows.slice(startIndex, endIndex)

  const handleScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop)
  }, [])

  const headerCellStyle: CSSProperties = {
    minWidth: 0,
    padding: '0 10px',
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '0.02em',
    textTransform: 'uppercase',
    color: 'var(--muted)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  }

  if (!columns.length) return null

  return (
    <div
      className="scroll-table"
      style={{
        maxHeight: height,
        overflowX: 'auto',
        overflowY: 'hidden',
      }}
    >
      <div style={{ minWidth: contentMinWidth }}>
        <div
          role="row"
          style={{
            height: HEADER_HEIGHT,
            display: 'grid',
            gridTemplateColumns,
            alignItems: 'center',
            borderBottom: '1px solid var(--border)',
            background: 'var(--panelAlt)',
            position: 'sticky',
            top: 0,
            zIndex: 2,
          }}
        >
          {columns.map(column => (
            <div key={column} role="columnheader" style={headerCellStyle} title={column}>
              {column}
            </div>
          ))}
        </div>

        <div
          role="rowgroup"
          style={{
            height: bodyHeight,
            overflowY: 'auto',
            position: 'relative',
            contain: 'strict',
          }}
          onScroll={handleScroll}
        >
          <div style={{ height: cappedRows.length * ROW_HEIGHT, position: 'relative' }}>
            {visibleRows.map((row, index) => (
              <DataRow
                key={`${startIndex + index}`}
                row={row}
                columns={columns}
                gridTemplateColumns={gridTemplateColumns}
                top={(startIndex + index) * ROW_HEIGHT}
              />
            ))}
          </div>
        </div>
      </div>

      {rows.length > maxRows && (
        <p style={{ padding: 8, fontSize: 11, color: 'var(--muted)' }}>
          Showing {maxRows} of {rows.length} rows
        </p>
      )}
    </div>
  )
}
