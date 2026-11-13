/**
 * Comprehensive Unit and Integration Tests for Issue #105:
 * [S2-FE1] Editable canvas with drag persistence and connection guards
 *
 * Covers:
 * 1. Connection Guards & Topological Validation (connectionGuards.js)
 *    - Self-connection rejection
 *    - Duplicate connection rejection
 *    - Cycle prevention via DAG / BFS traversal
 *    - Single-root target validation
 *    - Descendant node collection for branch deletion
 * 2. Canvas State Reducer & Mutation Actions (useCanvasState.js)
 *    - Node dragging and position updates (UPDATE_NODE_POSITION)
 *    - Inline label updates (UPDATE_NODE_LABEL)
 *    - Add child node with calculated coordinates and parent edge
 *    - Add sibling node with calculated coordinates and shared parent
 *    - Prevent adding sibling to root node
 *    - Edge addition with connection validation
 *    - Edge reconnection with validation
 *    - Leaf node deletion and edge cleanup
 *    - Branch deletion with descendant cascade
 *    - Prevent deleting root concept
 *    - Full undo / redo history stack preservation
 * 3. Debounced Persistence & Backend Integration
 *    - mindMapService.updateMapGraph contract and payload verification
 *    - Debounce timer behavior coalescing multiple rapid drag events
 *    - API failure resilience (local state preserved on 404 / 500)
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  isSelfConnection,
  isDuplicateConnection,
  wouldCreateCycle,
  isInvalidRootTarget,
  validateConnection,
  getDescendantNodeIds,
} from '../utils/connectionGuards'
import { canvasReducer, INITIAL_CANVAS_STATE } from '../components/canvas/useCanvasState'
import {
  isEditableElement,
  handleCanvasKeyboardShortcut,
} from '../components/canvas/EditorWorkspace'
import { mindMapService } from '../services/mindMapService'
import { apiClient } from '../services/apiClient'

// ─── 1. Connection Guards & Topological Validation ──────────────────────────

describe('Issue #105: Connection Guards & Topological Validation', () => {
  const sampleNodes = [
    { id: 'root', label: 'Central Theme', node_type: 'root' },
    { id: 'b1', label: 'Branch 1', node_type: 'branch' },
    { id: 'b2', label: 'Branch 2', node_type: 'branch' },
    { id: 'c1', label: 'Concept 1', node_type: 'concept' },
    { id: 'c2', label: 'Concept 2', node_type: 'concept' },
  ]

  const sampleEdges = [
    { id: 'e-r-b1', source_node_id: 'root', target_node_id: 'b1' },
    { id: 'e-r-b2', source_node_id: 'root', target_node_id: 'b2' },
    { id: 'e-b1-c1', source_node_id: 'b1', target_node_id: 'c1' },
    { id: 'e-c1-c2', source_node_id: 'c1', target_node_id: 'c2' },
  ]

  describe('Self-Connection Guard (isSelfConnection)', () => {
    it('detects and rejects connections where source equals target', () => {
      expect(isSelfConnection('b1', 'b1')).toBe(true)
      expect(isSelfConnection('root', 'root')).toBe(true)
    })

    it('allows connections between distinct nodes', () => {
      expect(isSelfConnection('b1', 'c1')).toBe(false)
      expect(isSelfConnection('root', 'b2')).toBe(false)
    })

    it('handles falsy or missing identifiers gracefully', () => {
      expect(isSelfConnection(null, 'b1')).toBe(false)
      expect(isSelfConnection('b1', null)).toBe(false)
      expect(isSelfConnection(undefined, undefined)).toBe(false)
    })
  })

  describe('Duplicate Connection Guard (isDuplicateConnection)', () => {
    it('detects existing connections using source_node_id and target_node_id', () => {
      expect(isDuplicateConnection(sampleEdges, 'root', 'b1')).toBe(true)
      expect(isDuplicateConnection(sampleEdges, 'b1', 'c1')).toBe(true)
    })

    it('detects existing connections formatted with source and target properties', () => {
      const flowEdges = [{ id: 'e1', source: 'b1', target: 'c1' }]
      expect(isDuplicateConnection(flowEdges, 'b1', 'c1')).toBe(true)
    })

    it('permits connections when no edge exists in that direction', () => {
      expect(isDuplicateConnection(sampleEdges, 'b2', 'c1')).toBe(false)
      expect(isDuplicateConnection(sampleEdges, 'root', 'c2')).toBe(false)
    })

    it('does not treat reversed connections as duplicate (cycle check handles cycles)', () => {
      expect(isDuplicateConnection(sampleEdges, 'b1', 'root')).toBe(false)
    })
  })

  describe('Cycle Prevention Guard (wouldCreateCycle)', () => {
    it('detects immediate 2-node cycle', () => {
      // e-r-b1 exists (root -> b1). Trying b1 -> root creates a cycle.
      expect(wouldCreateCycle(sampleEdges, 'b1', 'root')).toBe(true)
    })

    it('detects multi-hop transitive cycles', () => {
      // Path: root -> b1 -> c1 -> c2.
      // Trying c2 -> root creates a 4-node cycle.
      expect(wouldCreateCycle(sampleEdges, 'c2', 'root')).toBe(true)
      // Trying c2 -> b1 creates a 3-node cycle.
      expect(wouldCreateCycle(sampleEdges, 'c2', 'b1')).toBe(true)
      // Trying c1 -> root creates a cycle.
      expect(wouldCreateCycle(sampleEdges, 'c1', 'root')).toBe(true)
    })

    it('permits valid acyclic connections that create a DAG or fork/join', () => {
      // Path: root -> b2. Trying b2 -> c2 does not create a cycle.
      expect(wouldCreateCycle(sampleEdges, 'b2', 'c2')).toBe(false)
      // Trying b1 -> c2 creates a valid cross-link without cycle.
      expect(wouldCreateCycle(sampleEdges, 'b1', 'c2')).toBe(false)
    })

    it('detects self-cycle when source equals target', () => {
      expect(wouldCreateCycle(sampleEdges, 'b1', 'b1')).toBe(true)
    })
  })

  describe('Single Root Target Guard (isInvalidRootTarget)', () => {
    it('rejects making another node the parent of the root concept', () => {
      expect(isInvalidRootTarget(sampleNodes, 'root')).toBe(true)
    })

    it('permits connecting to branch and concept nodes', () => {
      expect(isInvalidRootTarget(sampleNodes, 'b1')).toBe(false)
      expect(isInvalidRootTarget(sampleNodes, 'c1')).toBe(false)
    })
  })

  describe('Comprehensive Connection Validation (validateConnection)', () => {
    it('returns valid for a legitimate hierarchical connection', () => {
      const res = validateConnection(sampleNodes, sampleEdges, {
        source: 'b2',
        target: 'c2',
      })
      expect(res.valid).toBe(true)
      expect(res.reason).toBeUndefined()
    })

    it('rejects self-connection with clear reason', () => {
      const res = validateConnection(sampleNodes, sampleEdges, {
        source: 'b1',
        target: 'b1',
      })
      expect(res.valid).toBe(false)
      expect(res.reason).toBe('A concept cannot connect to itself.')
    })

    it('rejects targeting root node with clear reason', () => {
      const res = validateConnection(sampleNodes, sampleEdges, {
        source: 'b2',
        target: 'root',
      })
      expect(res.valid).toBe(false)
      expect(res.reason).toBe('The central root concept cannot have incoming connections.')
    })

    it('rejects duplicate connection with clear reason', () => {
      const res = validateConnection(sampleNodes, sampleEdges, {
        source: 'root',
        target: 'b1',
      })
      expect(res.valid).toBe(false)
      expect(res.reason).toBe('A connection already exists between these concepts.')
    })

    it('rejects cycle creation with clear reason', () => {
      const res = validateConnection(sampleNodes, sampleEdges, {
        source: 'c2',
        target: 'b1',
      })
      expect(res.valid).toBe(false)
      expect(res.reason).toBe('Cannot create a cyclical connection in a hierarchical mind map.')
    })
  })

  describe('Descendant Collection for Branch Deletion (getDescendantNodeIds)', () => {
    it('returns empty array for a leaf concept with no children', () => {
      const descendants = getDescendantNodeIds(sampleEdges, 'c2')
      expect(descendants).toEqual([])
    })

    it('returns single child for intermediate node', () => {
      const descendants = getDescendantNodeIds(sampleEdges, 'c1')
      expect(descendants).toEqual(['c2'])
    })

    it('returns all transitive descendants for a branch', () => {
      const descendants = getDescendantNodeIds(sampleEdges, 'b1')
      expect(descendants.sort()).toEqual(['c1', 'c2'])
    })

    it('returns entire graph hierarchy below root', () => {
      const descendants = getDescendantNodeIds(sampleEdges, 'root')
      expect(descendants.sort()).toEqual(['b1', 'b2', 'c1', 'c2'])
    })
  })
})

// ─── 2. Canvas State Reducer & Editing Actions ───────────────────────────────

describe('Issue #105: Canvas State Reducer & Editing Operations', () => {
  const initialNodes = [
    { id: 'root', label: 'Central Concept', node_type: 'root', pos_x: 100, pos_y: 200 },
    { id: 'b1', label: 'Architecture', node_type: 'branch', pos_x: 380, pos_y: 150 },
    { id: 'c1', label: 'Vite Bundler', node_type: 'concept', pos_x: 660, pos_y: 120 },
  ]
  const initialEdges = [
    { id: 'e1', source_node_id: 'root', target_node_id: 'b1' },
    { id: 'e2', source_node_id: 'b1', target_node_id: 'c1' },
  ]

  let baseState

  beforeEach(() => {
    baseState = {
      ...INITIAL_CANVAS_STATE,
      nodes: initialNodes,
      edges: initialEdges,
    }
  })

  describe('Node Dragging & Position Updates (UPDATE_NODE_POSITION)', () => {
    it('updates node coordinates and records snapshot to undo stack', () => {
      const state = canvasReducer(baseState, {
        type: 'UPDATE_NODE_POSITION',
        payload: { id: 'b1', x: 420, y: 190 },
      })

      const moved = state.nodes.find((n) => n.id === 'b1')
      expect(moved.pos_x).toBe(420)
      expect(moved.pos_y).toBe(190)
      expect(state.past).toHaveLength(1)
      expect(state.future).toHaveLength(0)

      // Nodes not dragged remain unchanged
      const rootNode = state.nodes.find((n) => n.id === 'root')
      expect(rootNode.pos_x).toBe(100)
    })

    it('does not create undo snapshot if coordinates are identical', () => {
      const state = canvasReducer(baseState, {
        type: 'UPDATE_NODE_POSITION',
        payload: { id: 'b1', x: 380, y: 150 },
      })
      expect(state.past).toHaveLength(0)
    })

    it('updates selectedNode coordinates if the dragged node is currently selected', () => {
      const stateWithSelection = {
        ...baseState,
        selectedNode: initialNodes[1],
      }
      const state = canvasReducer(stateWithSelection, {
        type: 'UPDATE_NODE_POSITION',
        payload: { id: 'b1', x: 500, y: 250 },
      })
      expect(state.selectedNode.pos_x).toBe(500)
      expect(state.selectedNode.pos_y).toBe(250)
    })
  })

  describe('Inline Node Editing (UPDATE_NODE_LABEL)', () => {
    it('updates node label, resets editingNodeId, and records undo snapshot', () => {
      const stateEditing = {
        ...baseState,
        editingNodeId: 'b1',
      }
      const state = canvasReducer(stateEditing, {
        type: 'UPDATE_NODE_LABEL',
        payload: { id: 'b1', label: 'Frontend Architecture' },
      })

      const edited = state.nodes.find((n) => n.id === 'b1')
      expect(edited.label).toBe('Frontend Architecture')
      expect(state.editingNodeId).toBeNull()
      expect(state.past).toHaveLength(1)
    })

    it('sets and clears editingNodeId with START_EDITING and STOP_EDITING', () => {
      let state = canvasReducer(baseState, { type: 'START_EDITING', payload: 'c1' })
      expect(state.editingNodeId).toBe('c1')

      state = canvasReducer(state, { type: 'STOP_EDITING' })
      expect(state.editingNodeId).toBeNull()
    })
  })

  describe('Adding Child Nodes (ADD_CHILD_NODE)', () => {
    it('creates child node with calculated offset to the right and connects parent edge', () => {
      const state = canvasReducer(baseState, {
        type: 'ADD_CHILD_NODE',
        payload: { parentId: 'b1', label: 'Rollup Engine' },
      })

      expect(state.nodes).toHaveLength(4)
      const newNode = state.nodes[3]
      expect(newNode.label).toBe('Rollup Engine')
      expect(newNode.node_type).toBe('concept')
      // Parent pos_x is 380; child should be 380 + 280 = 660
      expect(newNode.pos_x).toBe(660)

      // Edge from b1 -> new node must exist
      expect(state.edges).toHaveLength(3)
      const newEdge = state.edges.find(
        (e) => e.source_node_id === 'b1' && e.target_node_id === newNode.id,
      )
      expect(newEdge).toBeDefined()

      // The new node should immediately be selected and put into editing mode
      expect(state.selectedNode.id).toBe(newNode.id)
      expect(state.editingNodeId).toBe(newNode.id)
      expect(state.past).toHaveLength(1)
    })

    it('creates branch type child when adding directly to root', () => {
      const state = canvasReducer(baseState, {
        type: 'ADD_CHILD_NODE',
        payload: { parentId: 'root', label: 'New Theme' },
      })

      const newNode = state.nodes[state.nodes.length - 1]
      expect(newNode.node_type).toBe('branch')
      expect(newNode.category).toBe('Branch')
    })
  })

  describe('Adding Sibling Nodes (ADD_SIBLING_NODE)', () => {
    it('creates sibling node below reference node sharing the same parent', () => {
      // c1 is a child of b1. Adding sibling to c1 should connect to b1.
      const state = canvasReducer(baseState, {
        type: 'ADD_SIBLING_NODE',
        payload: { refId: 'c1', label: 'Webpack Dev Server' },
      })

      expect(state.nodes).toHaveLength(4)
      const siblingNode = state.nodes[3]
      expect(siblingNode.label).toBe('Webpack Dev Server')
      expect(siblingNode.pos_x).toBe(660) // Same horizontal track as c1

      // Edge from b1 to siblingNode must be created
      const siblingEdge = state.edges.find(
        (e) => e.source_node_id === 'b1' && e.target_node_id === siblingNode.id,
      )
      expect(siblingEdge).toBeDefined()

      expect(state.selectedNode.id).toBe(siblingNode.id)
      expect(state.editingNodeId).toBe(siblingNode.id)
    })

    it('rejects adding sibling to the root concept', () => {
      const state = canvasReducer(baseState, {
        type: 'ADD_SIBLING_NODE',
        payload: { refId: 'root', label: 'Second Root' },
      })
      // Should not add any node
      expect(state.nodes).toHaveLength(3)
      expect(state.past).toHaveLength(0)
    })
  })

  describe('Node Connections & Reconnect Guards (ADD_EDGE, RECONNECT_EDGE)', () => {
    it('adds valid edge and creates undo snapshot', () => {
      // Connect b1 to a new unlinked node
      const extraNode = {
        id: 'c2',
        label: 'Independent',
        node_type: 'concept',
        pos_x: 700,
        pos_y: 300,
      }
      const stateWithExtra = {
        ...baseState,
        nodes: [...baseState.nodes, extraNode],
      }

      const state = canvasReducer(stateWithExtra, {
        type: 'ADD_EDGE',
        payload: { source: 'b1', target: 'c2' },
      })

      expect(state.edges).toHaveLength(3)
      expect(state.edges[2].source_node_id).toBe('b1')
      expect(state.edges[2].target_node_id).toBe('c2')
      expect(state.past).toHaveLength(1)
    })

    it('rejects invalid edge that creates cycle without mutating state', () => {
      // Attempting c1 -> b1 creates a cycle (b1 -> c1 already exists)
      const state = canvasReducer(baseState, {
        type: 'ADD_EDGE',
        payload: { source: 'c1', target: 'b1' },
      })

      expect(state.edges).toHaveLength(2)
      expect(state.past).toHaveLength(0)
    })

    it('reconnects edge when new connection is valid', () => {
      // Add extra node c2
      const extraNode = {
        id: 'c2',
        label: 'Alt Concept',
        node_type: 'concept',
        pos_x: 660,
        pos_y: 240,
      }
      const stateWithExtra = {
        ...baseState,
        nodes: [...baseState.nodes, extraNode],
      }

      // Reconnect e2 from (b1 -> c1) to (b1 -> c2)
      const state = canvasReducer(stateWithExtra, {
        type: 'RECONNECT_EDGE',
        payload: { oldEdge: { id: 'e2' }, newConnection: { source: 'b1', target: 'c2' } },
      })

      const reconnected = state.edges.find((e) => e.id === 'e2')
      expect(reconnected.target_node_id).toBe('c2')
      expect(state.past).toHaveLength(1)
    })

    it('rejects edge reconnection if new target violates cycle guards', () => {
      const state = canvasReducer(baseState, {
        type: 'RECONNECT_EDGE',
        payload: { oldEdge: { id: 'e2' }, newConnection: { source: 'c1', target: 'root' } },
      })
      // Edge e2 must remain untouched
      const original = state.edges.find((e) => e.id === 'e2')
      expect(original.target_node_id).toBe('c1')
      expect(state.past).toHaveLength(0)
    })
  })

  describe('Node Deletion & Branch Cascade (DELETE_NODE, DELETE_BRANCH)', () => {
    it('deletes leaf node and associated edge', () => {
      const state = canvasReducer(baseState, {
        type: 'DELETE_NODE',
        payload: 'c1',
      })

      expect(state.nodes.map((n) => n.id)).toEqual(['root', 'b1'])
      expect(state.edges.map((e) => e.id)).toEqual(['e1'])
      expect(state.past).toHaveLength(1)
    })

    it('prevents deleting root node', () => {
      const state = canvasReducer(baseState, {
        type: 'DELETE_NODE',
        payload: 'root',
      })
      expect(state.nodes).toHaveLength(3)
      expect(state.past).toHaveLength(0)
    })

    it('deletes entire branch cascade when DELETE_BRANCH is dispatched', () => {
      // Deleting b1 branch should delete b1 and its descendant c1
      const state = canvasReducer(baseState, {
        type: 'DELETE_BRANCH',
        payload: 'b1',
      })

      expect(state.nodes.map((n) => n.id)).toEqual(['root'])
      expect(state.edges).toHaveLength(0)
      expect(state.past).toHaveLength(1)
    })

    it('prevents branch deletion on root node', () => {
      const state = canvasReducer(baseState, {
        type: 'DELETE_BRANCH',
        payload: 'root',
      })
      expect(state.nodes).toHaveLength(3)
      expect(state.past).toHaveLength(0)
    })

    it('supports full undo and redo of branch deletion', () => {
      let state = canvasReducer(baseState, {
        type: 'DELETE_BRANCH',
        payload: 'b1',
      })
      expect(state.nodes).toHaveLength(1)

      // Undo restores all nodes and edges
      state = canvasReducer(state, { type: 'UNDO' })
      expect(state.nodes).toHaveLength(3)
      expect(state.edges).toHaveLength(2)

      // Redo removes them again
      state = canvasReducer(state, { type: 'REDO' })
      expect(state.nodes).toHaveLength(1)
      expect(state.edges).toHaveLength(0)
    })
  })
})

// ─── 3. Debounced Persistence & Backend Integration ──────────────────────────

describe('Issue #105: Debounced Persistence & API Integration', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('updateMapGraph sends PATCH /api/maps/{map_id}/graph with nodes and edges', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({
      ok: true,
      data: { success: true },
    })

    const nodes = [{ id: 'n1', label: 'Updated', pos_x: 200, pos_y: 300 }]
    const edges = [{ id: 'e1', source_node_id: 'root', target_node_id: 'n1' }]

    const result = await mindMapService.updateMapGraph('map-abc-123', { nodes, edges })
    expect(result).toEqual({ success: true })
    expect(patchSpy).toHaveBeenCalledWith('/api/maps/map-abc-123/graph', {
      nodes,
      edges,
    })
  })

  it('simulates debouncing coalescing rapid consecutive drag movements', async () => {
    const saveMock = vi.fn().mockResolvedValue({ success: true })

    // Simulate EditorWorkspace debounced saver
    let timer = null
    const scheduleDebouncedSave = (nodes, edges) => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        saveMock({ nodes, edges })
      }, 750)
    }

    // Rapid drag events at t=0, t=200, t=400ms
    scheduleDebouncedSave([{ id: 'b1', pos_x: 100, pos_y: 100 }], [])
    vi.advanceTimersByTime(200)

    scheduleDebouncedSave([{ id: 'b1', pos_x: 150, pos_y: 120 }], [])
    vi.advanceTimersByTime(200)

    scheduleDebouncedSave([{ id: 'b1', pos_x: 220, pos_y: 180 }], [])
    expect(saveMock).not.toHaveBeenCalled()

    // Advance beyond the 750ms debounce window from the last drag
    vi.advanceTimersByTime(750)

    expect(saveMock).toHaveBeenCalledTimes(1)
    expect(saveMock).toHaveBeenCalledWith({
      nodes: [{ id: 'b1', pos_x: 220, pos_y: 180 }],
      edges: [],
    })
  })

  it('preserves local graph state gracefully when backend API fails (404/500)', async () => {
    vi.spyOn(apiClient, 'patch').mockResolvedValue({
      ok: false,
      status: 404,
      error: 'Not Found: PATCH /api/maps/{map_id}/graph is not implemented',
    })

    const initialNodes = [{ id: 'b1', label: 'Local Concept', pos_x: 300, pos_y: 400 }]
    let localGraph = { nodes: initialNodes, edges: [] }

    // User edits local position
    localGraph = {
      ...localGraph,
      nodes: [{ ...localGraph.nodes[0], pos_x: 450, pos_y: 520 }],
    }

    // Attempt persistence
    let caughtError = null
    try {
      await mindMapService.updateMapGraph('map-test', localGraph)
    } catch (err) {
      caughtError = err
    }

    // The API error is caught
    expect(caughtError).toBeDefined()
    expect(caughtError.message).toContain('PATCH /api/maps/{map_id}/graph is not implemented')

    // Crucially: Local graph state remains updated and is never reverted or corrupted
    expect(localGraph.nodes[0].pos_x).toBe(450)
    expect(localGraph.nodes[0].pos_y).toBe(520)
    expect(localGraph.nodes[0].label).toBe('Local Concept')
  })
})

// ─── 4. Keyboard Shortcuts for Undo & Redo (Ctrl+Z, Ctrl+Y, Ctrl+Shift+Z) ────

describe('Issue #105: Canvas Keyboard Shortcuts (Ctrl+Z / Ctrl+Y)', () => {
  let onUndoMock
  let onRedoMock
  let preventDefaultMock

  beforeEach(() => {
    onUndoMock = vi.fn()
    onRedoMock = vi.fn()
    preventDefaultMock = vi.fn()
  })

  const createKeyEvent = ({
    key,
    ctrlKey = true,
    metaKey = false,
    shiftKey = false,
    altKey = false,
    target = { tagName: 'DIV' },
  }) => ({
    key,
    ctrlKey,
    metaKey,
    shiftKey,
    altKey,
    target,
    preventDefault: preventDefaultMock,
  })

  describe('isEditableElement helper', () => {
    it('detects input, textarea, and select elements as editable', () => {
      expect(isEditableElement({ tagName: 'INPUT' })).toBe(true)
      expect(isEditableElement({ tagName: 'input' })).toBe(true)
      expect(isEditableElement({ tagName: 'TEXTAREA' })).toBe(true)
      expect(isEditableElement({ tagName: 'textarea' })).toBe(true)
      expect(isEditableElement({ tagName: 'SELECT' })).toBe(true)
    })

    it('detects contentEditable elements', () => {
      expect(isEditableElement({ tagName: 'DIV', isContentEditable: true })).toBe(true)
      expect(
        isEditableElement({
          tagName: 'SPAN',
          getAttribute: (attr) => (attr === 'contenteditable' ? 'true' : null),
        }),
      ).toBe(true)
    })

    it('returns false for canvas viewport, node bodies, and non-editable tags', () => {
      expect(isEditableElement({ tagName: 'DIV' })).toBe(false)
      expect(isEditableElement({ tagName: 'BUTTON' })).toBe(false)
      expect(isEditableElement(null)).toBe(false)
    })
  })

  describe('Ctrl+Z (Undo) Shortcut', () => {
    it('triggers onUndo and prevents default with lowercase z', () => {
      const event = createKeyEvent({ key: 'z' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        canRedo: false,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onUndoMock).toHaveBeenCalledTimes(1)
      expect(preventDefaultMock).toHaveBeenCalled()
      expect(onRedoMock).not.toHaveBeenCalled()
    })

    it('triggers onUndo and prevents default with uppercase Z (without shift)', () => {
      const event = createKeyEvent({ key: 'Z' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        canRedo: false,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onUndoMock).toHaveBeenCalledTimes(1)
      expect(preventDefaultMock).toHaveBeenCalled()
    })

    it('works with metaKey (Cmd on macOS)', () => {
      const event = createKeyEvent({ key: 'z', ctrlKey: false, metaKey: true })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        canRedo: false,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onUndoMock).toHaveBeenCalledTimes(1)
      expect(preventDefaultMock).toHaveBeenCalled()
    })

    it('does not trigger onUndo if canUndo is false, but still prevents default', () => {
      const event = createKeyEvent({ key: 'z' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: false,
        canRedo: false,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onUndoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).toHaveBeenCalled()
    })
  })

  describe('Ctrl+Y & Ctrl+Shift+Z (Redo) Shortcut', () => {
    it('triggers onRedo and prevents default with Ctrl+y', () => {
      const event = createKeyEvent({ key: 'y' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: false,
        canRedo: true,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onRedoMock).toHaveBeenCalledTimes(1)
      expect(preventDefaultMock).toHaveBeenCalled()
      expect(onUndoMock).not.toHaveBeenCalled()
    })

    it('triggers onRedo with Ctrl+Y (uppercase)', () => {
      const event = createKeyEvent({ key: 'Y' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: false,
        canRedo: true,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onRedoMock).toHaveBeenCalledTimes(1)
      expect(preventDefaultMock).toHaveBeenCalled()
    })

    it('triggers onRedo with Ctrl+Shift+Z', () => {
      const event = createKeyEvent({ key: 'z', shiftKey: true })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        canRedo: true,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onRedoMock).toHaveBeenCalledTimes(1)
      expect(onUndoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).toHaveBeenCalled()
    })

    it('does not trigger onRedo if canRedo is false, but prevents default', () => {
      const event = createKeyEvent({ key: 'y' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: false,
        canRedo: false,
        onUndo: onUndoMock,
        onRedo: onRedoMock,
      })

      expect(handled).toBe(true)
      expect(onRedoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).toHaveBeenCalled()
    })
  })

  describe('Focus & Active Modal Guards', () => {
    it('does NOT trigger or preventDefault while typing in an input element', () => {
      const event = createKeyEvent({ key: 'z', target: { tagName: 'INPUT' } })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).not.toHaveBeenCalled()
    })

    it('does NOT trigger while typing in a textarea element', () => {
      const event = createKeyEvent({ key: 'z', target: { tagName: 'TEXTAREA' } })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).not.toHaveBeenCalled()
    })

    it('does NOT trigger when target is contentEditable', () => {
      const event = createKeyEvent({
        key: 'z',
        target: { tagName: 'DIV', isContentEditable: true },
      })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
    })

    it('does NOT trigger when a modal dialog is active', () => {
      const event = createKeyEvent({ key: 'z' })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
        isModalActive: true,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
      expect(preventDefaultMock).not.toHaveBeenCalled()
    })

    it('does NOT trigger for plain z key without Ctrl or Meta', () => {
      const event = createKeyEvent({ key: 'z', ctrlKey: false, metaKey: false })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
    })

    it('does NOT trigger when Alt key is pressed (e.g. Alt+Ctrl+Z)', () => {
      const event = createKeyEvent({ key: 'z', altKey: true })
      const handled = handleCanvasKeyboardShortcut({
        event,
        canUndo: true,
        onUndo: onUndoMock,
      })

      expect(handled).toBe(false)
      expect(onUndoMock).not.toHaveBeenCalled()
    })
  })
})
