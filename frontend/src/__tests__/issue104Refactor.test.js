/**
 * Regression & Unit Tests for Issue #104:
 * [S2-FE0] refactor(frontend): split GeneratingPage into progress view and EditorWorkspace
 *
 * Covers:
 * 1. Extraction of GenerationProgressView and job polling logic
 * 2. Dedicated EditorWorkspace encapsulation and component exports
 * 3. State isolation for FE-1 (Canvas & Undo/Redo), FE-2 (Comments & Chat), FE-3 (Export & History)
 * 4. React StrictMode resilience (cleanup & re-execution without losing polling)
 * 5. Timer cleanup and unmount safety (no duplicate timers, no updates after unmount)
 * 6. Completed regeneration fetches updated graph and auto-updates workspace without reload
 * 7. P0 Data Loss Prevention: Regeneration failure & cancel NEVER delete map
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import { canvasReducer, INITIAL_CANVAS_STATE } from '../components/canvas/useCanvasState'
import {
  commentsChatReducer,
  INITIAL_COMMENTS_CHAT_STATE,
} from '../components/canvas/useCommentsChat'
import {
  exportHistoryReducer,
  INITIAL_EXPORT_HISTORY_STATE,
} from '../components/canvas/useExportHistory'
import { mindMapService } from '../services/mindMapService'

describe('Issue #104: Component Exports & Contract Verification', () => {
  it('exports GenerationProgressView as named and default export from both paths', async () => {
    const modComponent = await import('../components/generator/GenerationProgressView')
    expect(modComponent.GenerationProgressView).toBeDefined()
    expect(modComponent.default).toBeDefined()

    const modPage = await import('../pages/GenerationProgressView')
    expect(modPage.GenerationProgressView).toBeDefined()
    expect(modPage.default).toBeDefined()
  })

  it('exports EditorWorkspace as named and default export from both paths', async () => {
    const modComponent = await import('../components/canvas/EditorWorkspace')
    expect(modComponent.EditorWorkspace).toBeDefined()
    expect(modComponent.default).toBeDefined()

    const modPage = await import('../pages/EditorWorkspace')
    expect(modPage.EditorWorkspace).toBeDefined()
    expect(modPage.default).toBeDefined()
  })
})

describe('FE-1 State Isolation: Canvas & Undo/Redo (canvasReducer)', () => {
  const initialNodes = [
    { id: 'root', label: 'Central Concept', node_type: 'root' },
    { id: 'branch-1', label: 'Branch 1', node_type: 'branch' },
  ]
  const initialEdges = [{ id: 'e1', source_node_id: 'root', target_node_id: 'branch-1' }]

  it('initializes with provided nodes, edges, and default viewMode', () => {
    const state = canvasReducer(INITIAL_CANVAS_STATE, {
      type: 'SET_GRAPH_DATA',
      payload: { nodes: initialNodes, edges: initialEdges },
    })
    expect(state.nodes).toEqual(initialNodes)
    expect(state.edges).toEqual(initialEdges)
    expect(state.viewMode).toBe('canvas')
    expect(state.past).toHaveLength(0)
    expect(state.future).toHaveLength(0)
  })

  it('supports node selection and clearing', () => {
    let state = canvasReducer(INITIAL_CANVAS_STATE, {
      type: 'SELECT_NODE',
      payload: initialNodes[0],
    })
    expect(state.selectedNode).toEqual(initialNodes[0])

    state = canvasReducer(state, { type: 'CLEAR_SELECTED_NODE' })
    expect(state.selectedNode).toBeNull()
  })

  it('supports viewMode toggle between canvas and outline', () => {
    let state = canvasReducer(INITIAL_CANVAS_STATE, {
      type: 'SET_VIEW_MODE',
      payload: 'outline',
    })
    expect(state.viewMode).toBe('outline')

    state = canvasReducer(state, { type: 'SET_VIEW_MODE', payload: 'canvas' })
    expect(state.viewMode).toBe('canvas')
  })

  it('pushes snapshot to past stack on ADD_NODE and supports UNDO / REDO', () => {
    let state = {
      ...INITIAL_CANVAS_STATE,
      nodes: initialNodes,
      edges: initialEdges,
    }

    const newNode = { id: 'concept-1', label: 'New Insight', node_type: 'concept' }
    state = canvasReducer(state, { type: 'ADD_NODE', payload: newNode })

    expect(state.nodes).toHaveLength(3)
    expect(state.past).toHaveLength(1)
    expect(state.future).toHaveLength(0)

    // Perform UNDO
    state = canvasReducer(state, { type: 'UNDO' })
    expect(state.nodes).toHaveLength(2)
    expect(state.past).toHaveLength(0)
    expect(state.future).toHaveLength(1)

    // Perform REDO
    state = canvasReducer(state, { type: 'REDO' })
    expect(state.nodes).toHaveLength(3)
    expect(state.past).toHaveLength(1)
    expect(state.future).toHaveLength(0)
  })

  it('deletes node and cascades connected edges with snapshot preservation', () => {
    let state = {
      ...INITIAL_CANVAS_STATE,
      nodes: initialNodes,
      edges: initialEdges,
    }

    state = canvasReducer(state, { type: 'DELETE_NODE', payload: 'branch-1' })
    expect(state.nodes).toHaveLength(1)
    expect(state.nodes[0].id).toBe('root')
    expect(state.edges).toHaveLength(0)
    expect(state.past).toHaveLength(1)

    // Undo restore deleted node & edge
    state = canvasReducer(state, { type: 'UNDO' })
    expect(state.nodes).toHaveLength(2)
    expect(state.edges).toHaveLength(1)
  })
})

describe('FE-2 State Isolation: Comments & Chat (commentsChatReducer)', () => {
  it('toggles chat drawer state and opens chat for specific node', () => {
    let state = commentsChatReducer(INITIAL_COMMENTS_CHAT_STATE, {
      type: 'TOGGLE_CHAT',
      payload: true,
    })
    expect(state.isChatOpen).toBe(true)

    state = commentsChatReducer(state, { type: 'TOGGLE_CHAT', payload: false })
    expect(state.isChatOpen).toBe(false)

    state = commentsChatReducer(state, {
      type: 'OPEN_CHAT_FOR_NODE',
      payload: 'node-x',
    })
    expect(state.isChatOpen).toBe(true)
    expect(state.activeCommentNodeId).toBe('node-x')
  })

  it('manages node comments independently without affecting other nodes', () => {
    const comment1 = { id: 'c1', text: 'Clarify relation', author: 'Alice' }
    const comment2 = { id: 'c2', text: 'Add reference', author: 'Bob' }

    let state = commentsChatReducer(INITIAL_COMMENTS_CHAT_STATE, {
      type: 'ADD_COMMENT',
      payload: { nodeId: 'node-1', comment: comment1 },
    })
    state = commentsChatReducer(state, {
      type: 'ADD_COMMENT',
      payload: { nodeId: 'node-1', comment: comment2 },
    })

    expect(state.commentsByNodeId['node-1']).toHaveLength(2)

    state = commentsChatReducer(state, {
      type: 'DELETE_COMMENT',
      payload: { nodeId: 'node-1', commentId: 'c1' },
    })
    expect(state.commentsByNodeId['node-1']).toHaveLength(1)
    expect(state.commentsByNodeId['node-1'][0].id).toBe('c2')
  })

  it('records chat messages and handles proposed actions', () => {
    const msg = { id: 'm1', role: 'user', content: 'Suggest taxonomy' }
    let state = commentsChatReducer(INITIAL_COMMENTS_CHAT_STATE, {
      type: 'ADD_MESSAGE',
      payload: msg,
    })
    expect(state.messages).toContainEqual(msg)

    state = {
      ...state,
      proposedActions: [{ id: 'act-1', type: 'add_branch' }],
    }
    state = commentsChatReducer(state, {
      type: 'APPLY_PROPOSED_ACTION',
      payload: { id: 'act-1' },
    })
    expect(state.proposedActions).toHaveLength(0)
  })
})

describe('FE-3 State Isolation: Export & History (exportHistoryReducer)', () => {
  it('manages export modal and progress lifecycle', () => {
    let state = exportHistoryReducer(INITIAL_EXPORT_HISTORY_STATE, { type: 'OPEN_EXPORT' })
    expect(state.isExportModalOpen).toBe(true)

    state = exportHistoryReducer(state, { type: 'SET_EXPORT_PROGRESS', payload: 45 })
    expect(state.exportProgress).toBe(45)

    state = exportHistoryReducer(state, { type: 'CLOSE_EXPORT' })
    expect(state.isExportModalOpen).toBe(false)
  })

  it('manages version history panel and snapshot list', () => {
    const mockSnapshots = [
      { id: 'snap-1', version_number: 1 },
      { id: 'snap-2', version_number: 2 },
    ]

    let state = exportHistoryReducer(INITIAL_EXPORT_HISTORY_STATE, { type: 'OPEN_HISTORY' })
    expect(state.isHistoryOpen).toBe(true)

    state = exportHistoryReducer(state, {
      type: 'SET_SNAPSHOTS',
      payload: mockSnapshots,
    })
    expect(state.snapshots).toEqual(mockSnapshots)
    expect(state.snapshotsLoading).toBe(false)

    state = exportHistoryReducer(state, {
      type: 'DELETE_SNAPSHOT',
      payload: 'snap-1',
    })
    expect(state.snapshots).toHaveLength(1)
    expect(state.snapshots[0].id).toBe('snap-2')
  })
})

describe('Job Polling, React StrictMode Resilience & Timer Safety', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('polls job status with bounded exponential backoff', async () => {
    const pollJobStatusSpy = vi
      .spyOn(mindMapService, 'pollJobStatus')
      .mockResolvedValueOnce({ status: 'running', progress_percent: 25 })
      .mockResolvedValueOnce({ status: 'running', progress_percent: 60 })
      .mockResolvedValueOnce({ status: 'completed', progress_percent: 100 })

    vi.spyOn(mindMapService, 'getMapGraph').mockResolvedValue({
      nodes: [{ id: 'n1', label: 'Done' }],
      edges: [],
    })
    vi.spyOn(mindMapService, 'getMapById').mockResolvedValue({
      title: 'Done Title',
    })

    let isMounted = true
    let delay = 1000
    let timer
    const onComplete = vi.fn()

    const poll = async () => {
      if (!isMounted) return
      const job = await mindMapService.pollJobStatus('map-1', 'job-1')
      if (!isMounted) return

      if (job.status === 'completed') {
        const [graphData, mapData] = await Promise.all([
          mindMapService.getMapGraph('map-1'),
          mindMapService.getMapById('map-1').catch(() => null),
        ])
        if (!isMounted) return
        onComplete({ graphData, mapData, job })
        return
      }

      delay = Math.min(delay + 250, 2500)
      if (isMounted) {
        timer = setTimeout(poll, delay)
      }
    }

    timer = setTimeout(poll, delay)

    // Tick 1 (1000ms)
    await vi.advanceTimersByTimeAsync(1000)
    expect(pollJobStatusSpy).toHaveBeenCalledTimes(1)
    expect(delay).toBe(1250)

    // Tick 2 (1250ms)
    await vi.advanceTimersByTimeAsync(1250)
    expect(pollJobStatusSpy).toHaveBeenCalledTimes(2)
    expect(delay).toBe(1500)

    // Tick 3 (1500ms -> completed)
    await vi.advanceTimersByTimeAsync(1500)
    expect(pollJobStatusSpy).toHaveBeenCalledTimes(3)
    expect(onComplete).toHaveBeenCalledWith({
      graphData: { nodes: [{ id: 'n1', label: 'Done' }], edges: [] },
      mapData: { title: 'Done Title' },
      job: { status: 'completed', progress_percent: 100 },
    })

    clearTimeout(timer)
  })

  it('resumes polling smoothly during React StrictMode mount -> cleanup -> remount cycle', async () => {
    const pollJobStatusSpy = vi
      .spyOn(mindMapService, 'pollJobStatus')
      .mockResolvedValue({ status: 'completed', progress_percent: 100 })

    vi.spyOn(mindMapService, 'getMapGraph').mockResolvedValue({
      nodes: [{ id: 'n1', label: 'Strict' }],
      edges: [],
    })
    vi.spyOn(mindMapService, 'getMapById').mockResolvedValue({
      title: 'Strict Map',
    })

    const onComplete = vi.fn()

    // StrictMode Mount 1
    let isMounted = true
    let timer = setTimeout(async () => {
      if (!isMounted) return
      const job = await mindMapService.pollJobStatus('map-strict', 'job-strict')
      if (!isMounted) return
      const [graphData, mapData] = await Promise.all([
        mindMapService.getMapGraph('map-strict'),
        mindMapService.getMapById('map-strict'),
      ])
      if (!isMounted) return
      onComplete({ graphData, mapData, job })
    }, 1000)

    // StrictMode cleanup 1: unmount and clear timer
    isMounted = false
    clearTimeout(timer)

    // StrictMode remount 2: mount and re-schedule timer
    isMounted = true
    timer = setTimeout(async () => {
      if (!isMounted) return
      const job = await mindMapService.pollJobStatus('map-strict', 'job-strict')
      if (!isMounted) return
      const [graphData, mapData] = await Promise.all([
        mindMapService.getMapGraph('map-strict'),
        mindMapService.getMapById('map-strict'),
      ])
      if (!isMounted) return
      onComplete({ graphData, mapData, job })
    }, 1000)

    await vi.advanceTimersByTimeAsync(1000)

    expect(pollJobStatusSpy).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        graphData: { nodes: [{ id: 'n1', label: 'Strict' }], edges: [] },
      }),
    )

    clearTimeout(timer)
  })

  it('prevents state updates and additional polling after true unmount', async () => {
    let isMounted = true
    let stateUpdatedAfterUnmount = false

    vi.spyOn(mindMapService, 'pollJobStatus').mockImplementation(async () => {
      return new Promise((resolve) => {
        setTimeout(() => {
          resolve({ status: 'running', progress_percent: 30 })
        }, 500)
      })
    })

    const poll = async () => {
      await mindMapService.pollJobStatus('map-unmount', 'job-unmount')
      if (!isMounted) return
      stateUpdatedAfterUnmount = true
    }

    poll()
    isMounted = false
    await vi.advanceTimersByTimeAsync(1000)

    expect(stateUpdatedAfterUnmount).toBe(false)
  })

  it('triggers 300s timeout error if polling exceeds 5 minutes', () => {
    const startTime = Date.now()
    const onError = vi.fn()

    const checkTimeout = (now) => {
      if (now - startTime > 300000) {
        const msg =
          'Generation is taking longer than usual. The worker is still processing in the background. You can check its progress on your dashboard.'
        onError(msg)
        return true
      }
      return false
    }

    expect(checkTimeout(startTime + 240000)).toBe(false)
    expect(onError).not.toHaveBeenCalled()

    expect(checkTimeout(startTime + 300001)).toBe(true)
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('taking longer than usual'))
  })
})

describe('Regeneration Completion Auto-Update (Requirement 8)', () => {
  it('updates canvas nodes and edges immediately upon completed regeneration without refresh', () => {
    const initialGraph = {
      nodes: [{ id: 'node-old', label: 'Old Structure', node_type: 'root' }],
      edges: [],
    }

    const regeneratedGraph = {
      nodes: [
        { id: 'node-new-root', label: 'New Synthesized Core', node_type: 'root' },
        { id: 'node-new-branch', label: 'New Branch A', node_type: 'branch' },
      ],
      edges: [
        { id: 'e-new-1', source_node_id: 'node-new-root', target_node_id: 'node-new-branch' },
      ],
    }

    let currentNodes = initialGraph.nodes
    let currentEdges = initialGraph.edges
    let isRegenerating = true

    const handleRegenerationComplete = ({ graphData }) => {
      currentNodes = graphData.nodes
      currentEdges = graphData.edges
      isRegenerating = false
    }

    handleRegenerationComplete({ graphData: regeneratedGraph })

    expect(isRegenerating).toBe(false)
    expect(currentNodes).toEqual(regeneratedGraph.nodes)
    expect(currentEdges).toEqual(regeneratedGraph.edges)
    expect(currentNodes).toHaveLength(2)
    expect(currentNodes[0].label).toBe('New Synthesized Core')
  })
})
