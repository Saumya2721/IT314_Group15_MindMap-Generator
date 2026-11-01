import React, { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import './nodes.css'

export const RootNode = memo(({ data, selected }) => {
  const tint = data?.backgroundColor || '#155E63'

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

      <div className="root-node-title">{data?.label || 'Central Concept'}</div>

      {data?.description && <div className="root-node-description">{data.description}</div>}
    </div>
  )
})

RootNode.displayName = 'RootNode'
