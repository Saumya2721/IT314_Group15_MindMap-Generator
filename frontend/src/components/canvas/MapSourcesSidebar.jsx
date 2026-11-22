import React, { useState, useMemo } from 'react'
import './MapSourcesSidebar.css'

/**
 * Aggregates all sources from the nodes of the current mind map.
 */
export function aggregateMapSources(nodes = []) {
  const sourceMap = new Map()

  nodes.forEach((node) => {
    // Read source metadata fields
    const type =
      node.source_type ||
      node.sourceType ||
      node.data?.source_type ||
      node.data?.sourceType ||
      (node.source_url?.includes('arxiv.org') ? 'arxiv' : null) ||
      (node.source_url?.includes('wikipedia.org') ? 'wikipedia' : null)

    const url =
      node.source_url || node.sourceUrl || node.data?.source_url || node.data?.sourceUrl || null

    const label =
      node.source_label ||
      node.sourceLabel ||
      node.data?.source_label ||
      node.data?.sourceLabel ||
      null

    if (!type && !url && !label) return

    const key = url || `${type || 'general'}:${label || node.label}`

    if (!sourceMap.has(key)) {
      sourceMap.set(key, {
        id: key,
        type: type || 'web_research',
        url,
        label: label || (url ? url.replace(/^https?:\/\//, '') : 'Grounded Material'),
        nodes: [node],
      })
    } else {
      sourceMap.get(key).nodes.push(node)
    }
  })

  return Array.from(sourceMap.values())
}

const TYPE_BADGES = {
  wikipedia: { label: 'Wikipedia', color: '#1e7145', bg: '#d9ead3', icon: 'article' },
  scholar: { label: 'Scholar', color: '#3949ab', bg: '#e8eaf6', icon: 'school' },
  arxiv: { label: 'arXiv Paper', color: '#6c3483', bg: '#e8daef', icon: 'menu_book' },
  page_reader: { label: 'Page Reader', color: '#00838f', bg: '#e0f7fa', icon: 'auto_stories' },
  web_research: { label: 'Web Research', color: '#1a5276', bg: '#d6eaf8', icon: 'travel_explore' },
  seed: { label: 'Seed Material', color: '#2d6a4f', bg: '#d8f3dc', icon: 'eco' },
  general: { label: 'General', color: '#7d6608', bg: '#fef9e7', icon: 'lightbulb' },
}

/**
 * S3-FE3: MapSourcesSidebar
 * Drawer / Flyout showing all aggregated research sources used in this map.
 */
export function MapSourcesSidebar({ isOpen, onClose, nodes = [], onSelectNode }) {
  const [filterType, setFilterType] = useState('all')

  const aggregatedSources = useMemo(() => aggregateMapSources(nodes), [nodes])

  const filteredSources = useMemo(() => {
    if (filterType === 'all') return aggregatedSources
    return aggregatedSources.filter((s) => s.type === filterType)
  }, [aggregatedSources, filterType])

  if (!isOpen) return null

  return (
    <aside
      className="map-sources-sidebar"
      role="complementary"
      aria-label="Map Sources & Research Transparency"
    >
      {/* Header */}
      <div className="sources-sidebar-header">
        <div className="sources-header-left">
          <span className="material-symbols-outlined sources-header-icon">find_in_page</span>
          <div>
            <h3 className="sources-sidebar-title">Research Sources</h3>
            <span className="sources-sidebar-subtitle">
              {aggregatedSources.length} external groundings
            </span>
          </div>
        </div>
        <button
          type="button"
          className="btn-close-sources"
          onClick={onClose}
          aria-label="Close sources panel"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
            close
          </span>
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="sources-filter-tabs">
        <button
          type="button"
          className={`sources-tab-btn ${filterType === 'all' ? 'active' : ''}`}
          onClick={() => setFilterType('all')}
        >
          All ({aggregatedSources.length})
        </button>
        {['wikipedia', 'scholar', 'arxiv', 'web_research', 'page_reader'].map((typeKey) => {
          const count = aggregatedSources.filter((s) => s.type === typeKey).length
          if (count === 0) return null
          return (
            <button
              key={typeKey}
              type="button"
              className={`sources-tab-btn ${filterType === typeKey ? 'active' : ''}`}
              onClick={() => setFilterType(typeKey)}
            >
              {TYPE_BADGES[typeKey]?.label || typeKey} ({count})
            </button>
          )
        })}
      </div>

      {/* Sources List */}
      <div className="sources-list-container">
        {filteredSources.length === 0 ? (
          <div className="sources-empty-state">
            <span className="material-symbols-outlined sources-empty-icon">menu_book</span>
            <p className="sources-empty-text">
              No specific research sources cited for this category. Concepts were derived from
              foundational synthesis.
            </p>
          </div>
        ) : (
          filteredSources.map((source) => {
            const badge = TYPE_BADGES[source.type] || TYPE_BADGES.general
            return (
              <div key={source.id} className="source-item-card">
                <div className="source-item-header">
                  <span
                    className="source-kind-pill"
                    style={{ backgroundColor: badge.bg, color: badge.color }}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: '12px' }}>
                      {badge.icon}
                    </span>
                    {badge.label}
                  </span>
                  {source.type === 'wikipedia' && (
                    <a
                      href="https://creativecommons.org/licenses/by-sa/4.0/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="wikipedia-cc-pill"
                      title="Licensed under Creative Commons Attribution-ShareAlike 4.0"
                    >
                      CC BY-SA 4.0
                    </a>
                  )}
                </div>

                <div className="source-title-wrap">
                  {source.url ? (
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="source-external-link"
                    >
                      <span>{source.label}</span>
                      <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                        open_in_new
                      </span>
                    </a>
                  ) : (
                    <span className="source-label-text">{source.label}</span>
                  )}
                </div>

                {/* Nodes referencing this source */}
                <div className="source-nodes-section">
                  <span className="source-nodes-label">
                    Cited in {source.nodes.length} concept{source.nodes.length > 1 ? 's' : ''}:
                  </span>
                  <div className="source-nodes-chips">
                    {source.nodes.map((node) => (
                      <button
                        key={node.id}
                        type="button"
                        className="source-node-chip"
                        onClick={() => onSelectNode?.(node)}
                        title="Focus this concept on canvas"
                      >
                        {node.label || node.title || 'Concept'}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </aside>
  )
}
