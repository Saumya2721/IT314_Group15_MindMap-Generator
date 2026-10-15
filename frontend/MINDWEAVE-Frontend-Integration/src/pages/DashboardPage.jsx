import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMindMap } from '../context/MindMapContext';
import { useAuth } from '../context/AuthContext';
import { AppHeader } from '../components/layout/AppHeader';
import { AppFooter } from '../components/layout/AppFooter';
import { MindMapCard } from '../components/dashboard/MindMapCard';
import './DashboardPage.css';

export default function DashboardPage() {
  const navigate = useNavigate();
  const { maps, loading, toggleBookmark } = useMindMap();
  const { user } = useAuth();

  // Search filter and sorting states
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState('recent'); // 'recent' | 'title' | 'nodes'

  // Filter and sort maps
  const filteredAndSortedMaps = useMemo(() => {
    let result = [...maps];

    // Filter by search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter(
        (m) =>
          m.title.toLowerCase().includes(q) ||
          m.category.toLowerCase().includes(q)
      );
    }

    // Sort maps
    if (sortBy === 'title') {
      result.sort((a, b) => a.title.localeCompare(b.title));
    } else if (sortBy === 'nodes') {
      result.sort((a, b) => b.nodeCount - a.nodeCount);
    }
    // 'recent' keeps original curated / lastEdited ordering

    return result;
  }, [maps, searchQuery, sortBy]);

  // Navigate to editor for selected mind map
  const handleCardClick = (id) => {
    navigate(`/editor/${id}`);
  };

  // Navigate to create page
  const handleCreateNewMap = () => {
    navigate('/create');
  };

  return (
    <div className="dashboard-layout">
      {/* Top Refined Header */}
      <AppHeader />

      {/* Main Workspace Body */}
      <main className="dashboard-main">
        {/* Hero Section: Editorial Welcome & Actions */}
        <section className="dashboard-hero">
          <div className="hero-left">
            <h1 className="hero-heading">Good to see you.</h1>
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
            <button
              type="button"
              className="btn-new-mind-map"
              onClick={handleCreateNewMap}
            >
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
              <option value="nodes">Sort by node count</option>
            </select>
            <span className="material-symbols-outlined sort-icon">expand_more</span>
          </div>
        </section>

        {/* Mind Maps Grid */}
        {filteredAndSortedMaps.length > 0 ? (
          <section className="maps-grid">
            {filteredAndSortedMaps.map((map) => (
              <MindMapCard
                key={map.id}
                map={map}
                onClick={handleCardClick}
                onToggleBookmark={toggleBookmark}
              />
            ))}
          </section>
        ) : (
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
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleCreateNewMap}
              >
                Create your first mind map
              </button>
            )}
          </div>
        )}
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  );
}
