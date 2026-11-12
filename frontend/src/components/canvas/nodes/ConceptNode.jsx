import React, { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import './nodes.css'

export const ConceptNode = memo(({ data, selected }) => {
  const tint = data?.backgroundColor || '#899B72'

  return (
    <div
      className={`atelier-flow-node concept-flow-node ${selected ? 'is-selected' : ''}`}
      style={{
        borderLeftColor: tint,
        borderLeftWidth: '3px',
        boxShadow: selected ? `0 0 0 2px ${tint}44, 0 4px 12px rgba(0, 0, 0, 0.06)` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} id="target-left" className="flow-handle" />
      <Handle type="source" position={Position.Right} id="source-right" className="flow-handle" />
      <Handle type="target" position={Position.Top} id="target-top" className="flow-handle" />
      <Handle type="source" position={Position.Bottom} id="source-bottom" className="flow-handle" />

      <div className="concept-node-title">{data?.label || 'Concept Node'}</div>

      {data?.description && <div className="concept-node-description">{data.description}</div>}
    </div>
  )
})

ConceptNode.displayName = 'ConceptNode'
