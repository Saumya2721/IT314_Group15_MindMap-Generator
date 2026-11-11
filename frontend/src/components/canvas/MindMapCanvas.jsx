import React, { useState, useMemo, useCallback, useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  getNodesBounds,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { RootNode } from './nodes/RootNode'
import { BranchNode } from './nodes/BranchNode'
import { ConceptNode } from './nodes/ConceptNode'
import { NodeInspector } from './NodeInspector'
import { adaptBackendGraphToFlow } from '../../utils/graphAdapter'
import './MindMapCanvas.css'

const nodeTypes = {
  rootNode: RootNode,
  branchNode: BranchNode,
  conceptNode: ConceptNode,
}

/**
 * Exportable React Flow instance wrapper.
 * Exposes the three imperative helpers the export service needs:
 *   - fitAllNodes(): refit the viewport so every node is visible
 *   - getViewport(): snapshot the current viewport for later restoration
 *   - setViewport(vp): restore a previously captured viewport
 */
function useExportableFlowApi(flowApiRef) {
  const rf = useReactFlow()

  useEffect(() => {
    if (!flowApiRef) return
    flowApiRef.current = {
      fitAllNodes: async () => {
        const nodes = rf.getNodes()
        if (nodes.length === 0) return
        const rect = getNodesBounds(nodes)
        const padding = 80
        const bounds = {
          x: rect.x - padding,
          y: rect.y - padding,
          width: rect.width + padding * 2,
          height: rect.height + padding * 2,
        }
        await rf.fitBounds(bounds, { duration: 0 })
      },
      getViewport: () => rf.getViewport(),
      setViewport: (viewport) => rf.setViewport(viewport),
    }
  }, [rf, flowApiRef])
}

function FlowCanvas({ rawNodes = [], rawEdges = [], toastMessage = '', onClearToast, flowApiRef }) {
  const [selectedNode, setSelectedNode] = useState(null)
  useExportableFlowApi(flowApiRef)

  // Transform backend graph nodes and edges into React Flow elements
  const { flowNodes, flowEdges } = useMemo(() => {
    return adaptBackendGraphToFlow(rawNodes, rawEdges)
  }, [rawNodes, rawEdges])

  // Handle node selection
  const handleNodeClick = useCallback((event, node) => {
    setSelectedNode({
      id: node.id,
      title: node.data?.label,
      label: node.data?.label,
      subtitle: node.data?.description,
      description: node.data?.description,
      tint: node.data?.backgroundColor,
      backgroundColor: node.data?.backgroundColor,
      category: node.data?.category,
      nodeType: node.data?.nodeType,
      incomingEdges: node.data?.incomingEdges,
      outgoingEdges: node.data?.outgoingEdges,
    })
  }, [])

  // Handle pane click to dismiss inspector
  const handlePaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  // Empty state handling
  if (!rawNodes || rawNodes.length === 0) {
    return (
      <div className="canvas-viewport-container empty-canvas-state">
        <div className="empty-canvas-content">
          <span className="material-symbols-outlined empty-canvas-icon">account_tree</span>
          <h3>No synthesized knowledge graph yet</h3>
          <p>Provide ideas or attach documents to begin the synthesis process.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="canvas-viewport-container" id="mindmap-canvas-export">
      {/* Toast Alert Feedback */}
      {toastMessage && (
        <div className="canvas-toast-alert">
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            check_circle
          </span>
          <span>{toastMessage}</span>
          {onClearToast && (
            <button
              type="button"
              onClick={onClearToast}
              style={{
                background: 'none',
                border: 'none',
                color: '#fff',
                cursor: 'pointer',
                padding: 0,
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                close
              </span>
            </button>
          )}
        </div>
      )}

      {/* React Flow Graph Engine */}
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.2}
        maxZoom={2}
        defaultEdgeOptions={{
          type: 'smoothstep',
        }}
      >
        <Background color="#b0b8b6" gap={24} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
        <MiniMap
          nodeStrokeColor="#155E63"
          maskColor="rgba(240, 245, 243, 0.75)"
          position="bottom-left"
          style={{
            borderRadius: '8px',
            overflow: 'hidden',
            border: '1px solid var(--outline-variant, #e1e3e0)',
          }}
        />
      </ReactFlow>

      {/* Flyout Inspector for Node Details */}
      {selectedNode && (
        <NodeInspector selectedNode={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
    </div>
  )
}

export function MindMapCanvas(props) {
  return (
    <ReactFlowProvider>
      <FlowCanvas
        rawNodes={props.rawNodes || props.initialNodes || []}
        rawEdges={props.rawEdges || []}
        toastMessage={props.toastMessage}
        onClearToast={props.onClearToast}
        flowApiRef={props.flowApiRef}
      />
    </ReactFlowProvider>
  )
}
