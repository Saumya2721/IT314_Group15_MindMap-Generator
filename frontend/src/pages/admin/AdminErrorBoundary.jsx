import React from 'react'

export class AdminErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error('Admin Panel Error Boundary caught:', error, errorInfo)
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          style={{
            padding: '32px',
            margin: '40px auto',
            maxWidth: '640px',
            backgroundColor: 'var(--surface-container-low, #1e2523)',
            borderRadius: '8px',
            border: '1px solid #ba1a1a',
            color: 'var(--on-surface, #e1e3e0)',
            textAlign: 'center',
          }}
        >
          <span
            className="material-symbols-outlined"
            style={{ fontSize: '48px', color: '#ffb4ab', marginBottom: '12px' }}
          >
            warning
          </span>
          <h2 style={{ fontFamily: 'var(--font-headline)', margin: '0 0 8px' }}>
            Administrative Panel Encountered an Issue
          </h2>
          <p
            style={{
              color: 'var(--ink-secondary, #a3aca9)',
              fontSize: '14px',
              marginBottom: '20px',
            }}
          >
            An unexpected error occurred while rendering the admin view.
          </p>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={this.handleReload}
            style={{
              padding: '8px 20px',
              backgroundColor: 'var(--primary)',
              color: '#ffffff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            Retry Loading
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
