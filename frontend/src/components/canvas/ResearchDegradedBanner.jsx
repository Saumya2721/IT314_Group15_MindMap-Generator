import React from 'react'
import './ResearchDegradedBanner.css'

/**
 * S3-FE3: ResearchDegradedBanner
 * Surfaces an alert banner when web research was degraded, timed out,
 * or unavailable during mind map generation.
 */
export function ResearchDegradedBanner({ reason, onDismiss, inline = false }) {
  if (!reason) return null

  return (
    <div
      className={`research-degraded-banner ${inline ? 'inline' : ''}`}
      role="alert"
      aria-live="polite"
    >
      <div className="banner-left">
        <span className="material-symbols-outlined banner-icon">travel_explore</span>
        <div className="banner-text">
          <strong className="banner-title">Web Research Notice:</strong>{' '}
          <span className="banner-desc">
            {typeof reason === 'string'
              ? reason
              : 'Live web and academic search was degraded or temporarily unavailable during synthesis. Graph concepts were completed using internal parametric knowledge.'}
          </span>
        </div>
      </div>
      {onDismiss && (
        <button
          type="button"
          className="btn-dismiss-banner"
          onClick={onDismiss}
          aria-label="Dismiss research notice"
          title="Dismiss"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            close
          </span>
        </button>
      )}
    </div>
  )
}
