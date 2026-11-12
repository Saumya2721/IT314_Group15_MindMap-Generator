import React from 'react'
import './DashboardSearchSkeleton.css'

/**
 * DashboardSearchSkeleton — loading skeleton for server-backed dashboard search
 * (S2-FE5 / US-08). Renders placeholder card skeletons while search results
 * stream in, or the empty-state panel when a search yields no results and
 * there are no maps to show.
 *
 * Props:
 *   - showingSkeleton: true when a search is loading with no results yet
 *   - searchQuery: current debounced query (drives empty-state copy)
 *   - maps: currently resolved maps (used for the no-results empty state)
 *   - onCreateNewMap: callback for the "create" empty-state CTA
 */
export function DashboardSearchSkeleton({ showingSkeleton, searchQuery, maps, onCreateNewMap }) {
  if (showingSkeleton) {
    return (
      <section className="dashboard-skeleton-grid" aria-busy="true" aria-live="polite">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton-card" aria-hidden="true">
            <div className="skeleton-line skeleton-title" />
            <div className="skeleton-line skeleton-meta" />
            <div className="skeleton-line skeleton-text" />
            <div className="skeleton-line skeleton-text-short" />
          </div>
        ))}
      </section>
    )
  }

  if (searchQuery.trim() && maps.length === 0) {
    return (
      <div className="dashboard-empty-state">
        <span className="material-symbols-outlined empty-state-icon">search_off</span>
        <h3 className="empty-state-title">No matching mind maps</h3>
        <p className="empty-state-text">
          {`We couldn't find any thought records matching "${searchQuery}". Try a different keyword or reset the search.`}
        </p>
        <button type="button" className="btn btn-secondary" onClick={onCreateNewMap}>
          Clear search filter
        </button>
      </div>
    )
  }

  if (maps.length === 0) {
    return (
      <div className="dashboard-empty-state">
        <span className="material-symbols-outlined empty-state-icon">account_tree</span>
        <h3 className="empty-state-title">No mind maps found</h3>
        <p className="empty-state-text">
          Start charting your ideas into structured visual knowledge.
        </p>
        <button type="button" className="btn btn-primary" onClick={onCreateNewMap}>
          Create your first mind map
        </button>
      </div>
    )
  }

  return null
}

export default DashboardSearchSkeleton
