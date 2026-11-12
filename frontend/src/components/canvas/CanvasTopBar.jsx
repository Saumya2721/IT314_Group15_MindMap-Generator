import React from 'react'
import { SaveStatusBadge } from './SaveStatusBadge'
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
  onHistory,
  onToggleChat,
  canUndo = false,
  canRedo = false,
  onUndo,
  onRedo,
  viewMode = 'canvas',
  onViewModeChange,
  saveStatus = 'saved',
  onSaveRetry,
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

        <span className="canvas-title-group">
          <input
            type="text"
            className="canvas-title-input"
            value={title || ''}
            onChange={(e) => onTitleChange && onTitleChange(e.target.value)}
            onBlur={onTitleBlur}
            placeholder="Untitled Map"
          />
          <SaveStatusBadge status={saveStatus} onRetry={onSaveRetry} />
        </span>
      </div>

      {/* Right: View Mode Toggle & Canvas Actions */}
      <div className="canvas-bar-right">
        {/* Undo / Redo Controls (FE-1) */}
        {onUndo && (
          <button
            type="button"
            className="btn-canvas-action"
            onClick={onUndo}
            disabled={!canUndo}
            title="Undo (Ctrl+Z)"
            aria-label="Undo canvas change"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              undo
            </span>
          </button>
        )}
        {onRedo && (
          <button
            type="button"
            className="btn-canvas-action"
            onClick={onRedo}
            disabled={!canRedo}
            title="Redo (Ctrl+Y)"
            aria-label="Redo canvas change"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              redo
            </span>
          </button>
        )}

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

        {/* History / Snapshots Button (FE-3) */}
        {onHistory && (
          <button
            type="button"
            className="btn-canvas-action"
            onClick={onHistory}
            title="Version History & Snapshots"
            aria-label="Version history"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              history
            </span>
            <span>History</span>
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

        {/* Chat Drawer Toggle (FE-2) */}
        {onToggleChat && (
          <button
            type="button"
            className="btn-canvas-action"
            onClick={onToggleChat}
            title="AI Chat & Discussion"
            aria-label="Toggle chat"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              chat
            </span>
          </button>
        )}

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
