import React, { useState, useEffect } from 'react'
import './EdgeInspector.css'

function getEdgeLabel(edge) {
  return (
    edge?.label ||
    edge?.data?.label ||
    edge?.relationship_label ||
    edge?.data?.relationship_label ||
    ''
  )
}

function getNodeLabel(node, fallback) {
  return (
    node?.label ||
    node?.title ||
    node?.data?.label ||
    node?.data?.title ||
    fallback
  )
}

export default function EdgeInspector({
  edge,
  sourceNode,
  targetNode,
  onSave,
  onDelete,
  onClose,
}) {
  const [label, setLabel] = useState(getEdgeLabel(edge))
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    setLabel(getEdgeLabel(edge))
  }, [edge])

  if (!edge) return null

  const handleSave = async (e) => {
    e.preventDefault()
    setIsSaving(true)
    try {
      const trimmed = label.trim()
      await onSave?.(edge.id, {
        label: trimmed,
        relationship_label: trimmed,
        edge_data: { ...(edge?.data || {}), label: trimmed, relationship_label: trimmed },
      })
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = () => {
    if (window.confirm('Are you sure you want to delete this edge connection?')) {
      onDelete?.(edge.id)
      onClose?.()
    }
  }

  return (
    <aside className="edge-inspector-flyout" role="region" aria-label="Edge Inspector">
      <div className="edge-inspector-header">
        <div className="edge-inspector-title-group">
          <span className="material-symbols-outlined edge-inspector-icon">timeline</span>
          <h3 className="edge-inspector-title">Edge Properties</h3>
        </div>
        <button
          type="button"
          className="edge-inspector-close-btn"
          onClick={onClose}
          aria-label="Close Edge Inspector"
        >
          <span className="material-symbols-outlined">close</span>
        </button>
      </div>

      <div className="edge-inspector-connection-card">
        <div className="edge-node-badge source">
          <span className="edge-badge-role">From</span>
          <span className="edge-badge-label">{getNodeLabel(sourceNode, 'Source Node')}</span>
        </div>
        <span className="material-symbols-outlined edge-arrow-icon">arrow_forward</span>
        <div className="edge-node-badge target">
          <span className="edge-badge-role">To</span>
          <span className="edge-badge-label">{getNodeLabel(targetNode, 'Target Node')}</span>
        </div>
      </div>

      <form onSubmit={handleSave} className="edge-inspector-form">
        <div className="edge-inspector-field">
          <label htmlFor="edge-label-input" className="edge-inspector-label">
            Edge Label / Relation Content
          </label>
          <input
            id="edge-label-input"
            type="text"
            className="edge-inspector-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g., depends on, leads to, formula: y = f(x)"
            autoFocus
          />
          <span className="edge-inspector-hint">
            Visible on the connection line on canvas. Supports formulas and relationship notes.
          </span>
        </div>

        <div className="edge-inspector-actions">
          <button
            type="button"
            className="edge-inspector-btn-delete"
            onClick={handleDelete}
            title="Delete this connection"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              delete
            </span>
            <span>Delete</span>
          </button>
          <button
            type="submit"
            className="edge-inspector-btn-save"
            disabled={isSaving}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              check
            </span>
            <span>{isSaving ? 'Saving…' : 'Save'}</span>
          </button>
        </div>
      </form>
    </aside>
  )
}
