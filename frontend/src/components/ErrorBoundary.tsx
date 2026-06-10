import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  name: string
  children: ReactNode
  fallback?: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  message: string
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  public constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = {
      hasError: false,
      message: '',
    }
  }

  public static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      message: error?.message ?? 'Unknown rendering error',
    }
  }

  public override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`[WidgetErrorBoundary:${this.props.name}]`, error, info)
  }

  public override render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children
    }

    if (this.props.fallback) {
      return this.props.fallback
    }

    return (
      <div
        className="card"
        style={{
          border: '1px solid rgba(248,113,113,0.28)',
          background: 'rgba(248,113,113,0.06)',
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>
          {this.props.name} failed locally
        </div>

        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 12 }}>
          The rest of the dashboard is still running.
        </div>

        <div
          style={{
            fontSize: 12,
            color: '#fca5a5',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {this.state.message}
        </div>
      </div>
    )
  }
}
