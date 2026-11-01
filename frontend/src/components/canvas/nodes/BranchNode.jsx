import React, { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import './nodes.css'

export const BranchNode = memo(({ data, selected }) => {
  const tint = data?.backgroundColor || '#C96B4B'

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

      <div className="branch-node-title">{data?.label || 'Thematic Branch'}</div>

      {data?.description && <div className="branch-node-description">{data.description}</div>}
    </div>
  )
})

BranchNode.displayName = 'BranchNode'
