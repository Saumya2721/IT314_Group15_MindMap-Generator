import React, { memo, useState, useEffect } from 'react'
import { Handle, Position } from '@xyflow/react'
import './nodes.css'

export const BranchNode = memo(({ id, data, selected }) => {
  const tint = data?.backgroundColor || '#C96B4B'
  const isEditing = Boolean(data?.isEditing)
  const [labelDraft, setLabelDraft] = useState(data?.label || 'Thematic Branch')

  useEffect(() => {
    setLabelDraft(data?.label || 'Thematic Branch')
  }, [data?.label, isEditing])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      if (labelDraft.trim()) {
        data?.onSaveLabel?.(id, labelDraft.trim())
      } else {
        data?.onCancelEditing?.(id)
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      setLabelDraft(data?.label || 'Thematic Branch')
      data?.onCancelEditing?.(id)
    }
  }

  const handleBlur = () => {
    if (labelDraft.trim()) {
      data?.onSaveLabel?.(id, labelDraft.trim())
    } else {
      data?.onCancelEditing?.(id)
    }
  }

  return (
    <div
      className={`atelier-flow-node branch-flow-node ${selected ? 'is-selected' : ''}`}
      style={{
        borderLeftColor: tint,
        borderLeftWidth: '4px',
        boxShadow: selected ? `0 0 0 2px ${tint}44, 0 6px 18px rgba(0, 0, 0, 0.08)` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} id="target-left" className="flow-handle" />
      <Handle type="source" position={Position.Right} id="source-right" className="flow-handle" />
      <Handle type="target" position={Position.Top} id="target-top" className="flow-handle" />
      <Handle type="source" position={Position.Bottom} id="source-bottom" className="flow-handle" />

      <div className="branch-node-header">
        <span className="flow-node-badge" style={{ backgroundColor: tint }}>
          {data?.category || 'Branch'}
        </span>
      </div>

      {isEditing ? (
        <input
          type="text"
          className="nodrag nopan node-inline-input"
          value={labelDraft}
          onChange={(e) => setLabelDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
          autoFocus
          aria-label="Edit branch title"
        />
      ) : (
        <div
          className="branch-node-title"
          onDoubleClick={(e) => {
            e.stopPropagation()
            data?.onStartEditing?.(id)
          }}
          title="Double-click to rename"
        >
          {data?.label || 'Thematic Branch'}
        </div>
      )}

      {data?.description && <div className="branch-node-description">{data.description}</div>}
    </div>
  )
})

BranchNode.displayName = 'BranchNode'
