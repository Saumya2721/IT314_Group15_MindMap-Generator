import React, { useState } from 'react'
import './AddNodeDialog.css'

const NODE_TYPES = [
  { value: 'concept', label: 'Concept (Detail / Sub-topic)', badge: 'Topic', color: '#899B72' },
  { value: 'branch', label: 'Branch (Major Category)', badge: 'Branch', color: '#C96B4B' },
  { value: 'root', label: 'Root (Central Theme)', badge: 'Root', color: '#2D5A47' },
]

export default function AddNodeDialog({
  isOpen,
  availableNodes = [],
  initialParentId = null,
  onConfirm,
  onCancel,
}) {
  const hasExistingRoot = availableNodes.some((n) => n.node_type === 'root')
  const rootNode = availableNodes.find((n) => n.node_type === 'root')

  // A mind map has exactly one central root. If a root exists, users can choose
  // between Branch (major sub-category) and Concept (detail / formula node).
  const selectableTypes = NODE_TYPES.filter((t) => t.value !== 'root' || !hasExistingRoot)

  const [label, setLabel] = useState('New Concept')
  const [connectToNodeId, setConnectToNodeId] = useState(initialParentId || '')
  const [nodeType, setNodeType] = useState(() => {
    const parent = availableNodes.find((n) => n.id === initialParentId)
    if (parent?.node_type === 'root') return 'branch'
    return hasExistingRoot ? 'concept' : 'root'
  })
  const [description, setDescription] = useState('')
  const [edgeLabel, setEdgeLabel] = useState('')

  if (!isOpen) return null

  const handleParentChange = (newParentId) => {
    setConnectToNodeId(newParentId)
    const parent = availableNodes.find((n) => n.id === newParentId)
    if (parent?.node_type === 'root') {
      setNodeType('branch')
    } else if (nodeType === 'root' && hasExistingRoot) {
      setNodeType('concept')
    }
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!label.trim()) return

    onConfirm({
      label: label.trim(),
      nodeType,
      description: description.trim(),
      connectToNodeId: connectToNodeId || null,
      edgeLabel: edgeLabel.trim(),
    })
  }

  return (
    <div className="add-node-backdrop" onClick={onCancel}>
      <div
        className="add-node-dialog"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="add-node-header">
          <div className="add-node-title-group">
            <span className="material-symbols-outlined add-node-header-icon">add_circle</span>
            <h2 className="add-node-title">Add Concept Node</h2>
          </div>
          <button
            type="button"
            className="add-node-close-btn"
            onClick={onCancel}
            aria-label="Close dialog"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="add-node-form">
          <div className="add-node-field">
            <label htmlFor="node-label-input" className="add-node-label">
              Concept Label <span className="required">*</span>
            </label>
            <input
              id="node-label-input"
              type="text"
              className="add-node-input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g., Cross-Entropy Loss"
              autoFocus
              required
            />
          </div>

          <div className="add-node-field">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <label className="add-node-label">Node Hierarchy Type</label>
              {hasExistingRoot && rootNode && (
                <span style={{ fontSize: '11px', color: 'var(--text-muted, #757773)' }}>
                  Root: <strong>{rootNode.label}</strong>
                </span>
              )}
            </div>
            <div className="add-node-types-grid">
              {selectableTypes.map((t) => {
                const isSelected = nodeType === t.value
                return (
                  <button
                    key={t.value}
                    type="button"
                    className={`add-node-type-card ${isSelected ? 'selected' : ''}`}
                    onClick={() => setNodeType(t.value)}
                  >
                    <span className="add-node-type-dot" style={{ backgroundColor: t.color }} />
                    <div className="add-node-type-info">
                      <span className="add-node-type-name">{t.badge}</span>
                      <span className="add-node-type-desc">{t.label}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="add-node-field">
            <label htmlFor="node-desc-input" className="add-node-label">
              Description / Mathematical Formulae (Optional)
            </label>
            <textarea
              id="node-desc-input"
              className="add-node-textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g., L(y, \hat{y}) = - \sum y_i \log(\hat{y}_i)"
              rows={3}
            />
          </div>

          <div className="add-node-field">
            <label htmlFor="node-connect-select" className="add-node-label">
              Connect to Existing Node (Optional)
            </label>
            <select
              id="node-connect-select"
              className="add-node-select"
              value={connectToNodeId}
              onChange={(e) => handleParentChange(e.target.value)}
            >
              <option value="">(None - Free Floating Node)</option>
              {availableNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label || n.title || n.data?.label || n.data?.title || 'Untitled Node'} (
                  {n.node_type || n.category || 'concept'})
                </option>
              ))}
            </select>
          </div>

          {connectToNodeId && (
            <div className="add-node-field">
              <label htmlFor="node-edge-label" className="add-node-label">
                Connecting Edge Label / Relation (Optional)
              </label>
              <input
                id="node-edge-label"
                type="text"
                className="add-node-input"
                value={edgeLabel}
                onChange={(e) => setEdgeLabel(e.target.value)}
                placeholder="e.g., requires, formulated as, extends"
              />
            </div>
          )}

          <div className="add-node-actions">
            <button type="button" className="add-node-btn-secondary" onClick={onCancel}>
              Cancel
            </button>
            <button type="submit" className="add-node-btn-primary">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                add
              </span>
              <span>Create Node</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
