import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
  useNodesState,
  useEdgesState,
  getNodesBounds,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { RootNode } from './nodes/RootNode'
import { BranchNode } from './nodes/BranchNode'
import { ConceptNode } from './nodes/ConceptNode'
import { adaptBackendGraphToFlow } from '../../utils/graphAdapter'
import { validateConnection } from '../../utils/connectionGuards'
import './MindMapCanvas.css'

const nodeTypes = {
  rootNode: RootNode,
  branchNode: BranchNode,
  conceptNode: ConceptNode,
}

/**
 * Exportable React Flow instance wrapper.
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

function FlowCanvas({
  rawNodes = [],
  rawEdges = [],
  toastMessage = '',
  onClearToast,
  onToast,
  flowApiRef,
  selectedNode = null,
  editingNodeId = null,
  onNodeSelect,
  onPaneClick,
  onNodePositionChange,
  onConnect,
  onReconnect,
  onStartEditing,
  onSaveLabel,
  onCancelEditing,
  onDeleteNode,
  onAddChild,
  onAddSibling,
}) {
  const [localSelectedNode, setLocalSelectedNode] = useState(null)
  const lastInvalidRef = useRef(null)
  useExportableFlowApi(flowApiRef)

  // Transform backend graph nodes and edges into React Flow elements
  const { flowNodes, flowEdges } = useMemo(() => {
    return adaptBackendGraphToFlow(rawNodes, rawEdges, {
      editingNodeId,
      onStartEditing,
      onSaveLabel,
      onCancelEditing,
    })
  }, [rawNodes, rawEdges, editingNodeId, onStartEditing, onSaveLabel, onCancelEditing])

  // React Flow state for smooth interactive manipulation (drag / connect)
  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges)

  useEffect(() => {
    setNodes(flowNodes)
  }, [flowNodes, setNodes])

  useEffect(() => {
    setEdges(flowEdges)
  }, [flowEdges, setEdges])

  // Handle node selection
  const handleNodeClick = useCallback(
    (event, node) => {
      const selected = {
        id: node.id,
        title: node.data?.label,
        label: node.data?.label,
        subtitle: node.data?.description,
        description: node.data?.description,
        tint: node.data?.backgroundColor,
        backgroundColor: node.data?.backgroundColor,
        category: node.data?.category,
        nodeType: node.data?.nodeType,
        pos_x: node.position?.x,
        pos_y: node.position?.y,
        incomingEdges: node.data?.incomingEdges,
        outgoingEdges: node.data?.outgoingEdges,
      }
      setLocalSelectedNode(selected)
      if (onNodeSelect) {
        onNodeSelect(selected)
      }
    },
    [onNodeSelect],
  )

  // Handle pane click to dismiss inspector and deselect
  const handlePaneClick = useCallback(() => {
    setLocalSelectedNode(null)
    if (onPaneClick) {
      onPaneClick()
    }
  }, [onPaneClick])

  // Node drag stop handler: commits new position
  const handleNodeDragStop = useCallback(
    (event, node) => {
      if (onNodePositionChange && node) {
        onNodePositionChange(node.id, Math.round(node.position.x), Math.round(node.position.y))
      }
    },
    [onNodePositionChange],
  )

  // Connection validation
  const isValidConnection = useCallback(
    (connection) => {
      const result = validateConnection(rawNodes, rawEdges, connection)
      if (!result.valid) {
        lastInvalidRef.current = { reason: result.reason, time: Date.now() }
        return false
      }
      lastInvalidRef.current = null
      return true
    },
    [rawNodes, rawEdges],
  )

  // Connect handler
  const handleConnect = useCallback(
    (connection) => {
      lastInvalidRef.current = null
      const result = validateConnection(rawNodes, rawEdges, connection)
      if (!result.valid) {
        if (onToast) onToast(result.reason)
        return
      }
      if (onConnect) {
        onConnect(connection)
      }
    },
    [rawNodes, rawEdges, onConnect, onToast],
  )

  // Edge reconnect handler
  const handleReconnect = useCallback(
    (oldEdge, newConnection) => {
      lastInvalidRef.current = null
      const filteredEdges = rawEdges.filter((e) => String(e.id) !== String(oldEdge.id || oldEdge))
      const result = validateConnection(rawNodes, filteredEdges, newConnection)
      if (!result.valid) {
        if (onToast) onToast(result.reason)
        return
      }
      if (onReconnect) {
        onReconnect(oldEdge, newConnection)
      }
    },
    [rawNodes, rawEdges, onReconnect, onToast],
  )

  // Releasing an invalid connection reports the validation warning
  const handleConnectEnd = useCallback(() => {
    if (lastInvalidRef.current) {
      const elapsed = Date.now() - lastInvalidRef.current.time
      if (elapsed < 600) {
        if (onToast) onToast(lastInvalidRef.current.reason)
      }
      lastInvalidRef.current = null
    }
  }, [onToast])

  // Keyboard navigation on canvas (Delete, Tab, Enter)
  const handleKeyDown = useCallback(
    (e) => {
      if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return

      const active = selectedNode || localSelectedNode
      if (!active) return

      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        if (onDeleteNode) {
          onDeleteNode(active.id)
        }
      } else if (e.key === 'Tab') {
        e.preventDefault()
        if (onAddChild) {
          onAddChild(active.id)
        }
      } else if (e.key === 'Enter') {
        e.preventDefault()
        if (onAddSibling) {
          onAddSibling(active.id)
        }
      }
    },
    [selectedNode, localSelectedNode, onDeleteNode, onAddChild, onAddSibling],
  )

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
    <div
      className="canvas-viewport-container"
      id="mindmap-canvas-export"
      onKeyDown={handleKeyDown}
      tabIndex={0}
      style={{ outline: 'none' }}
    >
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
              aria-label="Dismiss toast"
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
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={handleNodeDragStop}
        nodesDraggable={true}
        nodesConnectable={true}
        elementsSelectable={true}
        isValidConnection={isValidConnection}
        onConnect={handleConnect}
        onReconnect={handleReconnect}
        onConnectEnd={handleConnectEnd}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        onNodeDoubleClick={(event, node) => onStartEditing && onStartEditing(node.id)}
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
        onToast={props.onToast}
        flowApiRef={props.flowApiRef}
        selectedNode={props.selectedNode}
        editingNodeId={props.editingNodeId}
        onNodeSelect={props.onNodeSelect}
        onPaneClick={props.onPaneClick}
        onNodePositionChange={props.onNodePositionChange}
        onConnect={props.onConnect}
        onReconnect={props.onReconnect}
        onStartEditing={props.onStartEditing}
        onSaveLabel={props.onSaveLabel}
        onCancelEditing={props.onCancelEditing}
        onDeleteNode={props.onDeleteNode}
        onAddChild={props.onAddChild}
        onAddSibling={props.onAddSibling}
      />
    </ReactFlowProvider>
  )
}

export default MindMapCanvas
