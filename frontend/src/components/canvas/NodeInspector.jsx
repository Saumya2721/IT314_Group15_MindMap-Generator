import React, { useState, useEffect } from 'react'
import './NodeInspector.css'

export function NodeInspector({
  selectedNode,
  onClose,
  onUpdateLabel,
  onUpdateDescription,
  onAddChild,
  onAddSibling,
  onDeleteNode,
}) {
  const [labelDraft, setLabelDraft] = useState('')
  const [descDraft, setDescDraft] = useState('')

  useEffect(() => {
    if (selectedNode) {
      setLabelDraft(selectedNode.title || selectedNode.label || '')
      setDescDraft(selectedNode.description || selectedNode.subtitle || '')
    }
  }, [selectedNode])

  if (!selectedNode) return null

  const currentTint = selectedNode.tint || selectedNode.backgroundColor || '#155E63'
  const nodeType = selectedNode.nodeType || selectedNode.type || 'concept'
  const isRoot = nodeType === 'root' || nodeType === 'rootNode'
  const category = selectedNode.category || (isRoot ? 'Central Theme' : 'Concept')

  const handleLabelBlur = () => {
    if (labelDraft.trim() && labelDraft.trim() !== (selectedNode.title || selectedNode.label)) {
      onUpdateLabel?.(selectedNode.id, labelDraft.trim())
    }
  }

  const handleDescBlur = () => {
    if (descDraft !== (selectedNode.description || selectedNode.subtitle || '')) {
      onUpdateDescription?.(selectedNode.id, descDraft)
    }
  }

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

      {/* Editable Node Title */}
      <div className="inspector-field">
        <label className="inspector-field-label">Concept Title</label>
        <input
          type="text"
          value={labelDraft}
          onChange={(e) => setLabelDraft(e.target.value)}
          onBlur={handleLabelBlur}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.currentTarget.blur()
            }
          }}
          style={{
            width: '100%',
            padding: '8px 10px',
            borderRadius: '6px',
            border: '1px solid var(--structural-border, #e1e3e0)',
            fontSize: '14px',
            fontFamily: 'var(--font-headline, serif)',
            color: 'var(--on-surface, #181d1a)',
            fontWeight: 500,
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            boxSizing: 'border-box',
          }}
          aria-label="Edit node title"
        />
      </div>

      {/* Editable Description */}
      <div className="inspector-field" style={{ marginTop: '12px' }}>
        <label className="inspector-field-label">Description &amp; Reasoning</label>
        <textarea
          rows={3}
          value={descDraft}
          onChange={(e) => setDescDraft(e.target.value)}
          onBlur={handleDescBlur}
          placeholder="Add detailed explanation or reasoning for this node..."
          style={{
            width: '100%',
            padding: '8px 10px',
            borderRadius: '6px',
            border: '1px solid var(--structural-border, #e1e3e0)',
            fontSize: '13px',
            lineHeight: 1.5,
            fontFamily: 'var(--font-body, serif)',
            color: 'var(--on-surface-variant, #404947)',
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            boxSizing: 'border-box',
            resize: 'vertical',
          }}
          aria-label="Edit node description"
        />
      </div>

      {/* Quick Actions (Add Child, Add Sibling, Delete) */}
      <div style={{ marginTop: '16px' }}>
        <label className="inspector-field-label">Node Actions</label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}>
          {/* Add Child Node */}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => onAddChild?.(selectedNode.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              fontSize: '12.5px',
              padding: '7px 12px',
              width: '100%',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              subdirectory_arrow_right
            </span>
            <span>Add Child Concept (Tab)</span>
          </button>

          {/* Add Sibling Node (allowed for non-root nodes) */}
          {!isRoot && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => onAddSibling?.(selectedNode.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                fontSize: '12.5px',
                padding: '7px 12px',
                width: '100%',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                add_circle_outline
              </span>
              <span>Add Sibling Concept (Enter)</span>
            </button>
          )}

          {/* Delete Node (allowed for non-root nodes) */}
          {!isRoot && (
            <button
              type="button"
              onClick={() => onDeleteNode?.(selectedNode.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                fontSize: '12px',
                padding: '7px 12px',
                width: '100%',
                borderRadius: '6px',
                border: '1px solid #ffdad6',
                backgroundColor: '#fff8f7',
                color: '#ba1a1a',
                cursor: 'pointer',
                fontFamily: 'var(--font-label, sans-serif)',
                fontWeight: 500,
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                delete_outline
              </span>
              <span>Delete Node (Del)</span>
            </button>
          )}
        </div>
      </div>

      {/* Connections Info */}
      <div style={{ marginTop: '16px', fontSize: '12px', color: 'var(--outline, #6f7977)' }}>
        <div>
          Direct connections:{' '}
          {(selectedNode.incomingEdges?.length || 0) + (selectedNode.outgoingEdges?.length || 0)}{' '}
          links
        </div>
      </div>
    </div>
  )
}

export default NodeInspector
