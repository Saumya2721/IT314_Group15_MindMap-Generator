import React from 'react';
import './MindMapCard.css';

export function MindMapCard({ map, onClick, onToggleBookmark }) {
  const {
    id,
    title,
    category,
    categoryColor = '#00454a',
    nodeCount,
    lastEdited,
    bookmarked = false,
    previewData,
  } = map;

  const coreText = previewData?.core || title.slice(0, 10);
  const coreColor = previewData?.coreColor || categoryColor;
  const branches = previewData?.branches || [
    { label: 'Branch 1', x: 40, y: 30, color: categoryColor, side: 'left' },
    { label: 'Branch 2', x: 40, y: 70, color: categoryColor, side: 'left' },
    { label: 'Branch 3', x: 160, y: 30, color: categoryColor, side: 'right' },
    { label: 'Branch 4', x: 160, y: 70, color: categoryColor, side: 'right' },
  ];

  const handleBookmarkClick = (e) => {
    e.stopPropagation();
    if (onToggleBookmark) {
      onToggleBookmark(id);
    }
  };

  return (
    <div className="mindmap-card" onClick={() => onClick && onClick(id)}>
      {/* Top Title & Bookmark */}
      <div className="card-top">
        <h3 className="card-title">{title}</h3>
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
      </div>

      {/* Miniature Topological Thought Map Preview */}
      <div className="card-thumbnail">
        <svg
          className="card-thumbnail-svg"
          viewBox="0 0 200 100"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          {/* Connecting Curved Paths */}
          <path d="M100 50 C75 50 65 30 40 30" stroke="#bfc8c9" strokeWidth="1" />
          <path d="M100 50 C75 50 65 70 40 70" stroke="#bfc8c9" strokeWidth="1" />
          <path d="M100 50 C125 50 135 30 160 30" stroke="#bfc8c9" strokeWidth="1" />
          <path d="M100 50 C125 50 135 70 160 70" stroke="#bfc8c9" strokeWidth="1" />

          {/* Central Core Rect */}
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

          {/* Branch Nodes */}
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

      {/* Bottom Category Tag & Metadata */}
      <div className="card-bottom">
        <span className="card-tag">
          <span className="card-tag-dot" style={{ backgroundColor: categoryColor }} />
          <span className="card-tag-name">{category}</span>
        </span>
        <span className="card-meta-text">
          {nodeCount} nodes • {lastEdited}
        </span>
      </div>
    </div>
  );
}
