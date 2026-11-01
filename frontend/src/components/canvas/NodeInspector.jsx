import React from 'react'
import './NodeInspector.css'

export function NodeInspector({ selectedNode, onClose }) {
  if (!selectedNode) return null

  const currentTint = selectedNode.tint || selectedNode.backgroundColor || '#155E63'
  const nodeType = selectedNode.nodeType || selectedNode.type || 'concept'
  const category =
    selectedNode.category ||
    (nodeType === 'root' || nodeType === 'rootNode' ? 'Central Theme' : 'Concept')

  return (
    <div className="node-inspector-flyout">
      {/* Header */}
      <div className="inspector-header">
        <div className="inspector-header-left">
          <span className="dot" style={{ backgroundColor: currentTint }} />
          <span className="inspector-node-title">
            {selectedNode.title || selectedNode.label || 'Selected Node'}
          </span>
        </div>
        <button
          type="button"
          className="btn-close-inspector"
          onClick={onClose}
          aria-label="Close inspector"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            close
          </span>
        </button>
      </div>

      {/* Category / Type Badge */}
      <div style={{ marginBottom: '14px' }}>
        <span
          style={{
            display: 'inline-block',
            padding: '3px 8px',
            borderRadius: '4px',
            backgroundColor: `${currentTint}18`,
            color: currentTint,
            fontWeight: 600,
            fontSize: '11px',
            fontFamily: 'var(--font-label, sans-serif)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {category}
        </span>
      </div>

      {/* Read-Only Node Title */}
      <div className="inspector-field">
        <label className="inspector-field-label">Concept Title</label>
        <div
          style={{
            padding: '10px 12px',
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            borderRadius: '6px',
            fontSize: '14px',
            fontFamily: 'var(--font-headline, serif)',
            color: 'var(--on-surface, #181d1a)',
            fontWeight: 500,
          }}
        >
          {selectedNode.title || selectedNode.label || 'Untitled Concept'}
        </div>
      </div>

      {/* Read-Only Description */}
      <div className="inspector-field" style={{ marginTop: '12px' }}>
        <label className="inspector-field-label">Description &amp; Reasoning</label>
        <div
          style={{
            padding: '10px 12px',
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            borderRadius: '6px',
            fontSize: '13px',
            lineHeight: 1.5,
            fontFamily: 'var(--font-body, serif)',
            color: 'var(--on-surface-variant, #404947)',
            minHeight: '60px',
          }}
        >
          {selectedNode.subtitle ||
            selectedNode.description ||
            'No additional notes provided for this concept.'}
        </div>
      </div>

      {/* Connections Info */}
      {(selectedNode.incomingEdges?.length > 0 || selectedNode.outgoingEdges?.length > 0) && (
        <div style={{ marginTop: '14px', fontSize: '12px', color: 'var(--outline, #6f7977)' }}>
          <div>
            Direct connections:{' '}
            {(selectedNode.incomingEdges?.length || 0) + (selectedNode.outgoingEdges?.length || 0)}{' '}
            links
          </div>
        </div>
      )}

      {/* Sprint 2 Roadmap Notice */}
      <div
        style={{
          marginTop: '20px',
          padding: '10px 12px',
          borderRadius: '6px',
          backgroundColor: '#eff5f5',
          border: '1px solid #d4e3e3',
          fontSize: '11.5px',
          color: '#155e63',
          display: 'flex',
          gap: '8px',
          alignItems: 'flex-start',
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: '16px', marginTop: '1px' }}>
          info
        </span>
        <span>
          <strong>Sprint 1 Presentation Mode:</strong> Full interactive node mutation, canvas drag
          persistence, and node expansion will be enabled in Sprint 2.
        </span>
      </div>
    </div>
  )
}
