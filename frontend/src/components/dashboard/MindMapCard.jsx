import React from 'react'
import './MindMapCard.css'

export function MindMapCard({ map, onClick, onToggleBookmark, onDelete }) {
  const {
    id,
    title,
    is_public = false,
    lastEdited = 'Recently',
    bookmarked = false,
    previewData,
    node_count = 0,
    latest_job_status,
    latest_job_progress = 0,
  } = map

  const isGenerating = latest_job_status === 'running' || latest_job_status === 'pending'
  const progressPercent = Math.max(Number(latest_job_progress) || 15, 10)

  const coreText = previewData?.core || (title ? title.slice(0, 14) : 'Map')
  const coreColor = '#155e63'
  const branches = previewData?.branches || [
    { label: 'Branch 1', x: 40, y: 30, color: '#155e63', side: 'left' },
    { label: 'Branch 2', x: 40, y: 70, color: '#C96B4B', side: 'left' },
    { label: 'Branch 3', x: 160, y: 30, color: '#155e63', side: 'right' },
    { label: 'Branch 4', x: 160, y: 70, color: '#899B72', side: 'right' },
  ]

  const handleBookmarkClick = (e) => {
    e.stopPropagation()
    if (onToggleBookmark) {
      onToggleBookmark(id)
    }
  }

  const handleDeleteClick = (e) => {
    e.stopPropagation()
    if (onDelete) {
      onDelete(id, title)
    }
  }

  return (
    <div
      className={`mindmap-card ${isGenerating ? 'is-generating-card' : ''}`}
      onClick={() => onClick && onClick(id, map)}
    >
      {/* Top Title & Actions */}
      <div className="card-top">
        <h3 className="card-title">{title}</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            type="button"
            className={`card-bookmark-btn ${bookmarked ? 'active' : ''}`}
            onClick={handleBookmarkClick}
            aria-label={bookmarked ? 'Remove bookmark' : 'Bookmark map'}
            title={bookmarked ? 'Bookmarked' : 'Add to bookmarks'}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              {bookmarked ? 'bookmark' : 'bookmark_border'}
            </span>
          </button>
          {onDelete && (
            <button
              type="button"
              className="card-bookmark-btn"
              onClick={handleDeleteClick}
              aria-label="Delete mind map"
              title="Delete mind map"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                delete_outline
              </span>
            </button>
          )}
        </div>
      </div>

      {/* Middle: Active Synthesis Progress OR Topological Preview */}
      {isGenerating ? (
        <div className="card-generating-viewport">
          <div className="generating-badge-pill">
            <span className="material-symbols-outlined step-spin-icon" style={{ fontSize: '15px' }}>
              sync
            </span>
            <span>Synthesizing... {latest_job_progress || 20}%</span>
          </div>

          <div className="card-progress-track">
            <div className="card-progress-bar" style={{ width: `${progressPercent}%` }} />
          </div>

          <span className="card-live-notice">Click to watch live synthesis</span>
        </div>
      ) : (
        <div className="card-thumbnail">
          <svg
            className="card-thumbnail-svg"
            viewBox="0 0 200 100"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path d="M100 50 C75 50 65 30 40 30" stroke="#bfc8c9" strokeWidth="1" />
            <path d="M100 50 C75 50 65 70 40 70" stroke="#bfc8c9" strokeWidth="1" />
            <path d="M100 50 C125 50 135 30 160 30" stroke="#bfc8c9" strokeWidth="1" />
            <path d="M100 50 C125 50 135 70 160 70" stroke="#bfc8c9" strokeWidth="1" />

            <rect
              x="72"
              y="40"
              width="56"
              height="20"
              rx="3"
              fill="#ffffff"
              stroke="#bfc8c9"
              strokeWidth="1"
            />
            <text
              x="100"
              y="53"
              textAnchor="middle"
              fill={coreColor}
              fontSize="8px"
              fontWeight="600"
              fontFamily="Newsreader, serif"
            >
              {coreText}
            </text>

            {branches.map((b, idx) => (
              <g key={idx}>
                <circle cx={b.x} cy={b.y} r="3" fill={b.color} />
                <text
                  x={b.side === 'left' ? b.x - 6 : b.x + 6}
                  y={b.y + 3}
                  textAnchor={b.side === 'left' ? 'end' : 'start'}
                  fill="#181d1a"
                  fontSize="7px"
                  fontFamily="Plus Jakarta Sans, sans-serif"
                >
                  {b.label}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}

      {/* Bottom Metadata */}
      <div className="card-bottom">
        <span className="card-tag">
          <span
            className="card-tag-dot"
            style={{
              backgroundColor: isGenerating ? '#C96B4B' : is_public ? '#899B72' : '#155e63',
            }}
          />
          <span className="card-tag-name">
            {isGenerating ? 'Processing' : is_public ? 'Public' : 'Workspace'}
          </span>
        </span>
        <span className="card-meta-text">
          {isGenerating
            ? 'Background Worker Active'
            : node_count > 0
              ? `${node_count} ${node_count === 1 ? 'concept' : 'concepts'}`
              : `Edited ${lastEdited}`}
        </span>
      </div>
    </div>
  )
}
