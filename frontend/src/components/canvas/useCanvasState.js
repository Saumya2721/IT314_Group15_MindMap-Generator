import { useReducer, useCallback } from 'react'

export const INITIAL_CANVAS_STATE = {
  nodes: [],
  edges: [],
  selectedNode: null,
  viewMode: 'canvas',
  past: [],
  future: [],
}

export function canvasReducer(state, action) {
  switch (action.type) {
    case 'SET_GRAPH_DATA':
      return {
        ...state,
        nodes: action.payload.nodes || [],
        edges: action.payload.edges || [],
        selectedNode: null,
        past: [],
        future: [],
      }
    case 'SELECT_NODE':
      return { ...state, selectedNode: action.payload }
    case 'CLEAR_SELECTED_NODE':
      return { ...state, selectedNode: null }
    case 'SET_VIEW_MODE':
      return { ...state, viewMode: action.payload }
    case 'ADD_NODE': {
      const snap = { nodes: state.nodes, edges: state.edges }
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: [...state.nodes, action.payload],
      }
    }
    case 'UPDATE_NODE': {
      const snap = { nodes: state.nodes, edges: state.edges }
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: state.nodes.map((n) =>
          n.id === action.payload.id ? { ...n, ...action.payload.patch } : n,
        ),
      }
    }
    case 'DELETE_NODE': {
      const snap = { nodes: state.nodes, edges: state.edges }
      const id = action.payload
      return {
        ...state,
        past: [...state.past, snap].slice(-50),
        future: [],
        nodes: state.nodes.filter((n) => n.id !== id),
        edges: state.edges.filter((e) => e.source_node_id !== id && e.target_node_id !== id),
        selectedNode: state.selectedNode?.id === id ? null : state.selectedNode,
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
      }
    }
    default:
      return state
  }
}

/**
 * useCanvasState (FE-1: Canvas and Undo/Redo)
 *
 * Isolates all canvas viewport, node selection, view mode,
 * and undo/redo history state so FE-1 can be developed independently
 * without causing merge conflicts with comments, chat, export, or history.
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

  const addNode = useCallback((node) => {
    dispatch({ type: 'ADD_NODE', payload: node })
  }, [])

  const updateNode = useCallback((id, patch) => {
    dispatch({ type: 'UPDATE_NODE', payload: { id, patch } })
  }, [])

  const deleteNode = useCallback((id) => {
    dispatch({ type: 'DELETE_NODE', payload: id })
  }, [])

  const selectNode = useCallback((node) => {
    dispatch({ type: 'SELECT_NODE', payload: node })
  }, [])

  const clearSelectedNode = useCallback(() => {
    dispatch({ type: 'CLEAR_SELECTED_NODE' })
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
    viewMode: state.viewMode,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
    past: state.past,
    future: state.future,
    setGraphData,
    addNode,
    updateNode,
    deleteNode,
    selectNode,
    clearSelectedNode,
    setViewMode,
    undo,
    redo,
  }
}

export default useCanvasState
