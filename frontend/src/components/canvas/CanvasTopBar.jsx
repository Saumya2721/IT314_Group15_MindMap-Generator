import React from 'react'
import './CanvasTopBar.css'

export function CanvasTopBar({
  title,
  onTitleChange,
  onTitleBlur,
  onBack,
  onAddNode,
  onShare,
  onExport,
  onRegenerate,
  viewMode = 'canvas',
  onViewModeChange,
  isSaving = false,
}) {
  return (
    <div className="canvas-top-bar">
      {/* Left: Back Link & Editable Title */}
      <div className="canvas-bar-left">
        <button
          type="button"
          className="btn-back-dashboard"
          onClick={onBack}
          aria-label="Return to Dashboard"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            arrow_back
          </span>
          <span>Dashboard</span>
        </button>

        <span className="canvas-bar-divider" />

        <div className="canvas-title-group">
          <input
            type="text"
            className="canvas-title-input"
            value={title || ''}
            onChange={(e) => onTitleChange && onTitleChange(e.target.value)}
            onBlur={onTitleBlur}
            placeholder="Untitled Map"
          />
          <span className="canvas-save-status">
            <span
              className="save-dot"
              style={{ backgroundColor: isSaving ? '#C96B4B' : '#155e63' }}
            />
            <span>{isSaving ? 'Saving...' : 'Saved'}</span>
          </span>
        </div>
      </div>

      {/* Right: View Mode Toggle & Canvas Actions */}
      <div className="canvas-bar-right">
        {/* Canvas / Outline Toggle */}
        <div className="view-mode-switcher">
          <button
            type="button"
            className={`btn-mode-toggle ${viewMode === 'canvas' ? 'active' : ''}`}
            onClick={() => onViewModeChange && onViewModeChange('canvas')}
          >
            Canvas
          </button>
          <button
            type="button"
            className={`btn-mode-toggle ${viewMode === 'outline' ? 'active' : ''}`}
            onClick={() => onViewModeChange && onViewModeChange('outline')}
          >
            Outline
          </button>
        </div>

        {/* Regenerate Button */}
        {onRegenerate && (
          <button
            type="button"
            className="btn-canvas-action"
            onClick={onRegenerate}
            title="Regenerate whole map with AI"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              auto_awesome
            </span>
            <span>Regenerate</span>
          </button>
        )}

        {/* Share Button */}
        <button type="button" className="btn-canvas-action" onClick={onShare}>
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            ios_share
          </span>
          <span>Share</span>
        </button>

        {/* Export Button */}
        <button type="button" className="btn-canvas-action" onClick={onExport}>
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            download
          </span>
          <span>Export</span>
        </button>

        {/* Add Node Button */}
        {onAddNode && (
          <button
            type="button"
            className="btn-canvas-action btn-canvas-primary-action"
            onClick={onAddNode}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              add
            </span>
            <span>Add Node</span>
          </button>
        )}
      </div>
    </div>
  )
}
