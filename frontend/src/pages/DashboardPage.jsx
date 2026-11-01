import React, { useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMindMap } from '../context/MindMapContext'
import { useAuth } from '../context/AuthContext'
import { AppHeader } from '../components/layout/AppHeader'
import { AppFooter } from '../components/layout/AppFooter'
import { MindMapCard } from '../components/dashboard/MindMapCard'
import './DashboardPage.css'

export default function DashboardPage() {
  const navigate = useNavigate()
  const { maps, loading, error, fetchMaps, deleteMap, toggleBookmark } = useMindMap()
  const { user } = useAuth()

  // Search filter and sorting states
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState('recent') // 'recent' | 'title' | 'nodes'
  const [actionError, setActionError] = useState('')

  // Always refresh user's live maps on mounting the dashboard
  useEffect(() => {
    fetchMaps()
  }, [fetchMaps])

  // Auto-refresh maps periodically if any map is currently generating in background
  useEffect(() => {
    const hasGeneratingMap = maps.some(
      (m) => m.latest_job_status === 'running' || m.latest_job_status === 'pending',
    )
    if (!hasGeneratingMap) return

    const interval = setInterval(() => {
      fetchMaps()
    }, 3500)

    return () => clearInterval(interval)
  }, [maps, fetchMaps])

  // Filter and sort maps (excluding orphaned empty drafts that are not actively generating)
  const filteredAndSortedMaps = useMemo(() => {
    let result = maps.filter((m) => {
      const isGenerating = m.latest_job_status === 'running' || m.latest_job_status === 'pending'
      const hasNodes = (Number(m.node_count) || 0) > 0
      return hasNodes || isGenerating
    })

    // Filter by search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim()
      result = result.filter((m) => m.title && m.title.toLowerCase().includes(q))
    }

    // Sort maps
    if (sortBy === 'title') {
      result.sort((a, b) => (a.title || '').localeCompare(b.title || ''))
    }

    return result
  }, [maps, searchQuery, sortBy])

  // Navigate to editor for completed map OR to generating page for active synthesis
  const handleCardClick = (id, map) => {
    if (map?.latest_job_status === 'running' || map?.latest_job_status === 'pending') {
      navigate('/generating', {
        state: {
          mapId: id,
          jobId: map.latest_job_id,
          title: map.title,
          topic: map.title,
        },
      })
    } else {
      navigate(`/editor/${id}`)
    }
  }

  // Navigate to create page
  const handleCreateNewMap = () => {
    navigate('/create')
  }

  // Handle map deletion with user confirmation
  const handleDeleteMap = async (id, title) => {
    const confirmed = window.confirm(
      `Are you sure you want to permanently delete "${title || 'this mind map'}"? All nodes and generated branches will be removed.`,
    )
    if (!confirmed) return

    try {
      await deleteMap(id)
    } catch (err) {
      setActionError(err.message || 'Failed to delete mind map')
      setTimeout(() => setActionError(''), 4000)
    }
  }

  return (
    <div className="dashboard-layout">
      {/* Top Refined Header */}
      <AppHeader />

      {/* Main Workspace Body */}
      <main className="dashboard-main">
        {/* Error notification banner */}
        {(error || actionError) && (
          <div
            style={{
              padding: '12px 16px',
              marginBottom: '20px',
              backgroundColor: '#fed7d7',
              color: '#9b2c2c',
              borderRadius: '6px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              error
            </span>
            <span>{actionError || error}</span>
          </div>
        )}

        {/* Hero Section: Editorial Welcome & Actions */}
        <section className="dashboard-hero">
          <div className="hero-left">
            <h1 className="hero-heading">Good to see you{user?.name ? `, ${user.name}` : ''}.</h1>
            <p className="hero-subheading">Give your ideas somewhere to go.</p>
          </div>

          <div className="hero-actions">
            {/* Search Input Field */}
            <div className="search-wrapper">
              <span className="material-symbols-outlined search-icon">search</span>
              <input
                type="text"
                className="search-input"
                placeholder="Search maps..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              {searchQuery && (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => setSearchQuery('')}
                  aria-label="Clear search"
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                    close
                  </span>
                </button>
              )}
            </div>

            {/* Primary 'New mind map' CTA button */}
            <button type="button" className="btn-new-mind-map" onClick={handleCreateNewMap}>
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                add
              </span>
              <span>New mind map</span>
            </button>
          </div>
        </section>

        {/* Section Header: Recent Maps & Sorting Control */}
        <section className="dashboard-section-header">
          <div className="section-titles">
            <h2 className="section-heading">Recent maps</h2>
            <span className="section-tagline">Curated thought records</span>
          </div>

          <div className="sort-control">
            <select
              className="sort-select"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              aria-label="Sort mind maps"
            >
              <option value="recent">Sort by last edited</option>
              <option value="title">Sort by title</option>
            </select>
            <span className="material-symbols-outlined sort-icon">expand_more</span>
          </div>
        </section>

        {/* Loading Spinner */}
        {loading && maps.length === 0 && (
          <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--outline)' }}>
            <span className="material-symbols-outlined step-spin-icon" style={{ fontSize: '32px' }}>
              sync
            </span>
            <p style={{ marginTop: '12px' }}>Retrieving your cognitive workspaces...</p>
          </div>
        )}

        {/* Mind Maps Grid */}
        {filteredAndSortedMaps.length > 0 ? (
          <section className="maps-grid">
            {filteredAndSortedMaps.map((map) => (
              <MindMapCard
                key={map.id}
                map={map}
                onClick={handleCardClick}
                onToggleBookmark={toggleBookmark}
                onDelete={handleDeleteMap}
              />
            ))}
          </section>
        ) : !loading ? (
          /* Empty State */
          <div className="dashboard-empty-state">
            <span className="material-symbols-outlined empty-state-icon">
              {searchQuery ? 'search_off' : 'account_tree'}
            </span>
            <h3 className="empty-state-title">
              {searchQuery ? 'No matching mind maps' : 'No mind maps found'}
            </h3>
            <p className="empty-state-text">
              {searchQuery
                ? `We couldn't find any thought records matching "${searchQuery}". Try a different keyword or reset the search.`
                : 'Start charting your ideas into structured visual knowledge.'}
            </p>
            {searchQuery ? (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setSearchQuery('')}
              >
                Clear search filter
              </button>
            ) : (
              <button type="button" className="btn btn-primary" onClick={handleCreateNewMap}>
                Create your first mind map
              </button>
            )}
          </div>
        ) : null}
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  )
}
