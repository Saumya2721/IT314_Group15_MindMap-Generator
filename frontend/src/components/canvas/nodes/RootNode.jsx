import React, { memo, useState, useEffect } from 'react'
import { Handle, Position } from '@xyflow/react'
import './nodes.css'

export const RootNode = memo(({ id, data, selected }) => {
  const tint = data?.backgroundColor || '#155E63'
  const isEditing = Boolean(data?.isEditing)
  const [labelDraft, setLabelDraft] = useState(data?.label || 'Central Concept')

  useEffect(() => {
    setLabelDraft(data?.label || 'Central Concept')
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
      setLabelDraft(data?.label || 'Central Concept')
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
      className={`atelier-flow-node root-flow-node ${selected ? 'is-selected' : ''}`}
      style={{
        borderColor: tint,
        boxShadow: selected ? `0 0 0 3px ${tint}33, 0 8px 24px rgba(21, 94, 99, 0.15)` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} id="target-left" className="flow-handle" />
      <Handle type="source" position={Position.Right} id="source-right" className="flow-handle" />
      <Handle type="source" position={Position.Bottom} id="source-bottom" className="flow-handle" />
      <Handle type="target" position={Position.Top} id="target-top" className="flow-handle" />

      <div className="flow-node-badge" style={{ backgroundColor: tint }}>
        <span>{data?.category || 'Core Focus'}</span>
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
          aria-label="Edit central concept title"
        />
      ) : (
        <div
          className="root-node-title"
          onDoubleClick={(e) => {
            e.stopPropagation()
            data?.onStartEditing?.(id)
          }}
          title="Double-click to rename"
        >
          {data?.label || 'Central Concept'}
        </div>
      )}

      {data?.description && <div className="root-node-description">{data.description}</div>}
    </div>
  )
})

RootNode.displayName = 'RootNode'
