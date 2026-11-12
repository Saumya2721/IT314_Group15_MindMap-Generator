import { useReducer, useCallback } from 'react'
import { validateConnection, getDescendantNodeIds } from '../../utils/connectionGuards'

export const INITIAL_CANVAS_STATE = {
  nodes: [],
  edges: [],
  selectedNode: null,
  editingNodeId: null,
  viewMode: 'canvas',
  past: [],
  future: [],
}

/**
 * Helper to compute sensible child position to the right of parent.
 */
function calculateChildPosition(parentNode, existingNodes = [], edges = []) {
  const parentX = Number(parentNode?.pos_x) || 80
  const parentY = Number(parentNode?.pos_y) || 200

  // Find all current children of this parent
  const parentId = String(parentNode.id)
  const childIds = new Set(
    edges
      .filter((e) => String(e.source_node_id || e.source) === parentId)
      .map((e) => String(e.target_node_id || e.target)),
  )

  const childNodes = existingNodes.filter((n) => childIds.has(String(n.id)))

  const posX = parentX + 280
  let posY = parentY

  if (childNodes.length > 0) {
    // Place below the lowest existing child
    const maxY = Math.max(...childNodes.map((n) => Number(n.pos_y) || parentY))
    posY = maxY + 110
  }

  return { x: posX, y: posY }
}

/**
 * Helper to compute sensible sibling position below reference node.
 */
function calculateSiblingPosition(refNode, existingNodes = [], edges = []) {
  const refX = Number(refNode?.pos_x) || 80
  const refY = Number(refNode?.pos_y) || 200

  // Find parent of refNode
  const refId = String(refNode.id)
  const incomingEdge = edges.find((e) => String(e.target_node_id || e.target) === refId)

  let posY = refY + 110

  if (incomingEdge) {
    const parentId = String(incomingEdge.source_node_id || incomingEdge.source)
    const siblingIds = new Set(
      edges
        .filter((e) => String(e.source_node_id || e.source) === parentId)
        .map((e) => String(e.target_node_id || e.target)),
    )
    const siblings = existingNodes.filter((n) => siblingIds.has(String(n.id)))
    if (siblings.length > 0) {
      const maxY = Math.max(...siblings.map((n) => Number(n.pos_y) || refY))
      posY = Math.max(posY, maxY + 110)
    }
  }

  return { x: refX, y: posY }
}

export function canvasReducer(state, action) {
  switch (action.type) {
    case 'SET_GRAPH_DATA':
      return {
        ...state,
        nodes: action.payload.nodes || [],
        edges: action.payload.edges || [],
        selectedNode: null,
        editingNodeId: null,
        past: [],
        future: [],
      }

    case 'SELECT_NODE':
      return {
        ...state,
        selectedNode: action.payload,
      }

    case 'CLEAR_SELECTED_NODE':
      return {
        ...state,
        selectedNode: null,
        editingNodeId: null,
      }

    case 'START_EDITING':
      return {
        ...state,
        editingNodeId: String(action.payload),
      }

    case 'STOP_EDITING':
      return {
        ...state,
        editingNodeId: null,
      }

    case 'SET_VIEW_MODE':
      return {
        ...state,
        viewMode: action.payload,
      }

    case 'UPDATE_NODE_POSITION': {
      const { id, x, y } = action.payload
      const nodeId = String(id)
      const targetNode = state.nodes.find((n) => String(n.id) === nodeId)
      if (
        !targetNode ||
        (Number(targetNode.pos_x) === Number(x) && Number(targetNode.pos_y) === Number(y))
      ) {
        return state
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      const updatedNodes = state.nodes.map((n) =>
        String(n.id) === nodeId ? { ...n, pos_x: Number(x), pos_y: Number(y) } : n,
      )

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: updatedNodes,
        selectedNode:
          state.selectedNode && String(state.selectedNode.id) === nodeId
            ? { ...state.selectedNode, pos_x: Number(x), pos_y: Number(y) }
            : state.selectedNode,
      }
    }

    case 'UPDATE_NODE_LABEL': {
      const { id, label } = action.payload
      const nodeId = String(id)
      const targetNode = state.nodes.find((n) => String(n.id) === nodeId)
      if (!targetNode || targetNode.label === label) {
        return { ...state, editingNodeId: null }
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      const updatedNodes = state.nodes.map((n) =>
        String(n.id) === nodeId ? { ...n, label: label.trim() } : n,
      )

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: updatedNodes,
        editingNodeId: null,
        selectedNode:
          state.selectedNode && String(state.selectedNode.id) === nodeId
            ? { ...state.selectedNode, label: label.trim(), title: label.trim() }
            : state.selectedNode,
      }
    }

    case 'UPDATE_NODE': {
      const snap = { nodes: state.nodes, edges: state.edges }
      const nodeId = String(action.payload.id)
      const updatedNodes = state.nodes.map((n) =>
        String(n.id) === nodeId ? { ...n, ...action.payload.patch } : n,
      )

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: updatedNodes,
        selectedNode:
          state.selectedNode && String(state.selectedNode.id) === nodeId
            ? { ...state.selectedNode, ...action.payload.patch }
            : state.selectedNode,
      }
    }

    case 'ADD_NODE': {
      const snap = { nodes: state.nodes, edges: state.edges }
      const newNode = action.payload
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: [...state.nodes, newNode],
        selectedNode: newNode,
      }
    }

    case 'ADD_CHILD_NODE': {
      const parentId = String(action.payload.parentId)
      const parentNode = state.nodes.find((n) => String(n.id) === parentId)
      if (!parentNode) return state

      const snap = { nodes: state.nodes, edges: state.edges }
      const isParentRoot = parentNode.node_type === 'root'
      const childType = isParentRoot ? 'branch' : 'concept'
      const childTint = isParentRoot ? '#C96B4B' : '#899B72'
      const childCategory = isParentRoot ? 'Branch' : 'Topic'
      const pos = calculateChildPosition(parentNode, state.nodes, state.edges)

      const childId = `node-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      const newChildNode = {
        id: childId,
        label: action.payload.label || 'New Concept',
        description: '',
        node_type: childType,
        background_color: childTint,
        category: childCategory,
        pos_x: pos.x,
        pos_y: pos.y,
      }

      const newEdge = {
        id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        source_node_id: parentId,
        target_node_id: childId,
        label: '',
      }

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: [...state.nodes, newChildNode],
        edges: [...state.edges, newEdge],
        selectedNode: newChildNode,
        editingNodeId: childId,
      }
    }

    case 'ADD_SIBLING_NODE': {
      const refId = String(action.payload.refId)
      const refNode = state.nodes.find((n) => String(n.id) === refId)
      if (!refNode || refNode.node_type === 'root') {
        // Root cannot have siblings
        return state
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      const incomingEdge = state.edges.find((e) => String(e.target_node_id || e.target) === refId)
      const pos = calculateSiblingPosition(refNode, state.nodes, state.edges)

      const siblingId = `node-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      const siblingNode = {
        id: siblingId,
        label: action.payload.label || 'New Concept',
        description: '',
        node_type: refNode.node_type || 'concept',
        background_color: refNode.background_color || '#899B72',
        category: refNode.category || 'Topic',
        pos_x: pos.x,
        pos_y: pos.y,
      }

      const newEdges = [...state.edges]
      if (incomingEdge) {
        const parentId = String(incomingEdge.source_node_id || incomingEdge.source)
        newEdges.push({
          id: `edge-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          source_node_id: parentId,
          target_node_id: siblingId,
          label: '',
        })
      }

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: [...state.nodes, siblingNode],
        edges: newEdges,
        selectedNode: siblingNode,
        editingNodeId: siblingId,
      }
    }

    case 'ADD_EDGE': {
      const connection = action.payload
      const validation = validateConnection(state.nodes, state.edges, connection)
      if (!validation.valid) {
        return state
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      const newEdge = {
        id: connection.id || `edge-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        source_node_id: String(connection.source_node_id || connection.source),
        target_node_id: String(connection.target_node_id || connection.target),
        label: connection.label || '',
      }

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        edges: [...state.edges, newEdge],
      }
    }

    case 'RECONNECT_EDGE': {
      const { oldEdge, newConnection } = action.payload
      const filteredEdges = state.edges.filter(
        (e) => String(e.id) !== String(oldEdge.id || oldEdge),
      )
      const validation = validateConnection(state.nodes, filteredEdges, newConnection)
      if (!validation.valid) {
        return state
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      const updatedEdges = state.edges.map((e) => {
        if (String(e.id) === String(oldEdge.id || oldEdge)) {
          return {
            ...e,
            source_node_id: String(newConnection.source_node_id || newConnection.source),
            target_node_id: String(newConnection.target_node_id || newConnection.target),
          }
        }
        return e
      })

      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        edges: updatedEdges,
      }
    }

    case 'DELETE_NODE': {
      const id = String(action.payload)
      const nodeToDelete = state.nodes.find((n) => String(n.id) === id)
      if (!nodeToDelete || nodeToDelete.node_type === 'root') {
        // Root node cannot be deleted
        return state
      }

      const snap = { nodes: state.nodes, edges: state.edges }
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: state.nodes.filter((n) => String(n.id) !== id),
        edges: state.edges.filter(
          (e) =>
            String(e.source_node_id || e.source) !== id &&
            String(e.target_node_id || e.target) !== id,
        ),
        selectedNode:
          state.selectedNode && String(state.selectedNode.id) === id ? null : state.selectedNode,
        editingNodeId: state.editingNodeId === id ? null : state.editingNodeId,
      }
    }

    case 'DELETE_BRANCH': {
      const rootDeleteId = String(action.payload)
      const nodeToDelete = state.nodes.find((n) => String(n.id) === rootDeleteId)
      if (!nodeToDelete || nodeToDelete.node_type === 'root') {
        return state
      }

      const descendantIds = getDescendantNodeIds(state.edges, rootDeleteId)
      const allDeleteIds = new Set([rootDeleteId, ...descendantIds])

      const snap = { nodes: state.nodes, edges: state.edges }
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: state.nodes.filter((n) => !allDeleteIds.has(String(n.id))),
        edges: state.edges.filter((e) => {
          const s = String(e.source_node_id || e.source)
          const t = String(e.target_node_id || e.target)
          return !allDeleteIds.has(s) && !allDeleteIds.has(t)
        }),
        selectedNode:
          state.selectedNode && allDeleteIds.has(String(state.selectedNode.id))
            ? null
            : state.selectedNode,
        editingNodeId:
          state.editingNodeId && allDeleteIds.has(state.editingNodeId) ? null : state.editingNodeId,
      }
    }

    case 'UNDO': {
      if (state.past.length === 0) return state
      const prev = state.past[state.past.length - 1]
      const newPast = state.past.slice(0, -1)
      return {
        ...state,
        past: newPast,
        future: [{ nodes: state.nodes, edges: state.edges }, ...state.future],
        nodes: prev.nodes,
        edges: prev.edges,
        selectedNode: null,
        editingNodeId: null,
      }
    }

    case 'REDO': {
      if (state.future.length === 0) return state
      const next = state.future[0]
      const newFuture = state.future.slice(1)
      return {
        ...state,
        past: [...state.past, { nodes: state.nodes, edges: state.edges }],
        future: newFuture,
        nodes: next.nodes,
        edges: next.edges,
        selectedNode: null,
        editingNodeId: null,
      }
    }

    default:
      return state
  }
}

/**
 * useCanvasState (FE-1: Canvas, Dragging, Editing, and Undo/Redo)
 *
 * Encapsulates all canvas node mutation, dragging coordinates,
 * edge connection guards, branch deletion, and undo/redo stacks.
 */
export function useCanvasState({
  initialNodes = [],
  initialEdges = [],
  initialViewMode = 'canvas',
} = {}) {
  const [state, dispatch] = useReducer(canvasReducer, {
    ...INITIAL_CANVAS_STATE,
    nodes: initialNodes,
    edges: initialEdges,
    viewMode: initialViewMode,
  })

  const setGraphData = useCallback(({ nodes, edges }) => {
    dispatch({ type: 'SET_GRAPH_DATA', payload: { nodes, edges } })
  }, [])

  const updateNodePosition = useCallback((id, x, y) => {
    dispatch({ type: 'UPDATE_NODE_POSITION', payload: { id, x, y } })
  }, [])

  const updateNodeLabel = useCallback((id, label) => {
    dispatch({ type: 'UPDATE_NODE_LABEL', payload: { id, label } })
  }, [])

  const updateNode = useCallback((id, patch) => {
    dispatch({ type: 'UPDATE_NODE', payload: { id, patch } })
  }, [])

  const addNode = useCallback((node) => {
    dispatch({ type: 'ADD_NODE', payload: node })
  }, [])

  const addChildNode = useCallback((parentId, label) => {
    dispatch({ type: 'ADD_CHILD_NODE', payload: { parentId, label } })
  }, [])

  const addSiblingNode = useCallback((refId, label) => {
    dispatch({ type: 'ADD_SIBLING_NODE', payload: { refId, label } })
  }, [])

  const addEdge = useCallback((connection) => {
    dispatch({ type: 'ADD_EDGE', payload: connection })
  }, [])

  const reconnectEdge = useCallback((oldEdge, newConnection) => {
    dispatch({ type: 'RECONNECT_EDGE', payload: { oldEdge, newConnection } })
  }, [])

  const deleteNode = useCallback((id) => {
    dispatch({ type: 'DELETE_NODE', payload: id })
  }, [])

  const deleteBranch = useCallback((id) => {
    dispatch({ type: 'DELETE_BRANCH', payload: id })
  }, [])

  const selectNode = useCallback((node) => {
    dispatch({ type: 'SELECT_NODE', payload: node })
  }, [])

  const clearSelectedNode = useCallback(() => {
    dispatch({ type: 'CLEAR_SELECTED_NODE' })
  }, [])

  const startEditing = useCallback((nodeId) => {
    dispatch({ type: 'START_EDITING', payload: nodeId })
  }, [])

  const stopEditing = useCallback(() => {
    dispatch({ type: 'STOP_EDITING' })
  }, [])

  const setViewMode = useCallback((mode) => {
    dispatch({ type: 'SET_VIEW_MODE', payload: mode })
  }, [])

  const undo = useCallback(() => {
    dispatch({ type: 'UNDO' })
  }, [])

  const redo = useCallback(() => {
    dispatch({ type: 'REDO' })
  }, [])

  return {
    nodes: state.nodes,
    edges: state.edges,
    selectedNode: state.selectedNode,
    editingNodeId: state.editingNodeId,
    viewMode: state.viewMode,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
    past: state.past,
    future: state.future,
    setGraphData,
    updateNodePosition,
    updateNodeLabel,
    updateNode,
    addNode,
    addChildNode,
    addSiblingNode,
    addEdge,
    reconnectEdge,
    deleteNode,
    deleteBranch,
    selectNode,
    clearSelectedNode,
    startEditing,
    stopEditing,
    setViewMode,
    undo,
    redo,
  }
}

export default useCanvasState
