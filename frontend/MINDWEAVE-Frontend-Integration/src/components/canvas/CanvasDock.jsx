import React from 'react';
import './CanvasDock.css';

export function CanvasDock({ zoom = 100, onZoomIn, onZoomOut, onFitScreen, onAutoLayout }) {
  return (
    <div className="canvas-control-dock">
      {/* Zoom Out */}
      <button
        type="button"
        className="btn-dock-icon"
        onClick={onZoomOut}
        title="Zoom Out"
        aria-label="Zoom out"
      >
        <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
          remove
        </span>
      </button>

      {/* Current Zoom Level */}
      <span className="dock-zoom-level">{zoom}%</span>

      {/* Zoom In */}
      <button
        type="button"
        className="btn-dock-icon"
        onClick={onZoomIn}
        title="Zoom In"
        aria-label="Zoom in"
      >
        <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
          add
        </span>
      </button>

      <span className="dock-divider" />

      {/* Fit to Screen */}
      <button
        type="button"
        className="btn-dock-icon"
        onClick={onFitScreen}
        title="Fit to Screen"
        aria-label="Fit to screen"
      >
        <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
          fit_screen
        </span>
      </button>

      {/* Auto-Layout */}
      <button
        type="button"
        className="btn-dock-action"
        onClick={onAutoLayout}
        title="Automatically arrange nodes into hierarchical tree"
      >
        <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
          account_tree
        </span>
        <span>Auto-Layout</span>
      </button>
    </div>
  );
}
