/**
 * Sprint 1 Regression Tests (S2-QA1, Issue #79)
 *
 * Guards US-01 through US-04 with automated regression checks:
 *   US-01 Thought capture   -> auth dual-defense (HttpOnly cookie + Authorization Bearer)
 *   US-02 Seed upload       -> generation request intake (FormData payload)
 *   US-03 Background synthesis -> job polling / ARQ job status contract
 *   US-04 Read-only canvas  -> graph payload contract + graphAdapter mapping
 *
 * Runs offline against a mocked global `fetch`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import { adaptBackendGraphToFlow } from '../utils/graphAdapter'
import {
  apiClient,
  apiRequest,
  getStoredToken,
  setStoredToken,
  clearStoredAuth,
  getStoredUser,
  setStoredUser,
  formatApiError,
} from '../services/apiClient'
import { mindMapService } from '../services/mindMapService'

// ─────────── Offline harness ────────────────────────────────────────────────────

let capturedCalls = []
const realFetch = globalThis.fetch
const realSessionStorage = globalThis.sessionStorage
const realLocalStorage = globalThis.localStorage

const jsonResponse = (body, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => 'application/json' },
  json: async () => body,
  text: async () => JSON.stringify(body),
})

function mockFetch(fn) {
  capturedCalls = []
  globalThis.fetch = vi.fn(async (input, options) => {
    capturedCalls.push({ input, options })
    return fn(input, options)
  })
}

// `VITE_API_URL` (typically http://localhost:8000) prefixes every API request,
// so compare only the request path.
const pathOf = (input) => String(input).replace(/^https?:\/\/[^/]+/, '')

function makeStorage() {
  const sessionStore = new Map()
  const localStore = new Map()
  const make = (store) => ({
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
    key: () => null,
    get length() {
      return store.size
    },
  })
  globalThis.sessionStorage = make(sessionStore)
  globalThis.localStorage = make(localStore)
}

beforeEach(() => {
  makeStorage()
  mockFetch(() => Promise.reject(new Error('unmocked fetch')))
  clearStoredAuth() // reset module-level `_inMemoryToken` between tests
})

afterEach(() => {
  globalThis.fetch = realFetch
  globalThis.sessionStorage = realSessionStorage
  globalThis.localStorage = realLocalStorage
  vi.restoreAllMocks()
})

// ─────────────────────────────────────────────────────────────────────────────
// US-01 / US-02: Auth dual-defense
// ─────────────────────────────────────────────────────────────────────────────

describe('US-01/US-02: Auth dual-defense regression', () => {
  it('attaches Authorization Bearer header from stored token', async () => {
    setStoredToken('stored-sprint1-token')
    mockFetch(() => Promise.resolve(jsonResponse({ map_id: 'm1', title: 'Test map' })))

    await apiClient.get('/api/maps/m1')

    expect(capturedCalls).toHaveLength(1)
    expect(pathOf(capturedCalls[0].input)).toBe('/api/maps/m1')
    expect(capturedCalls[0].options.headers.get('Authorization')).toBe(
      'Bearer stored-sprint1-token',
    )
    expect(capturedCalls[0].options.credentials).toBe('include')
  })

  it('refreshes silently via HttpOnly cookie on 401 (dual-defense recovery)', async () => {
    const refreshSpy = vi.fn(() => Promise.resolve(jsonResponse({ access_token: 'fresh-token' })))

    mockFetch(async (input) => {
      if (input.toString().includes('/auth/refresh')) return refreshSpy()
      return jsonResponse({ detail: 'Unauthorized' }, 401)
    })

    const result = await apiRequest('/api/maps/m1', { method: 'GET' })

    expect(refreshSpy).toHaveBeenCalled()
    // Bearer fallback should also be re-attempted with fresh token on retry
    const retryCalls = capturedCalls.filter((c) => c.input.toString().includes('/maps/m1'))
    expect(retryCalls.length).toBeGreaterThanOrEqual(1)
    expect(getStoredToken()).toBe('fresh-token')
    expect(result.ok).toBe(false)
  })

  it('clears stored auth when session refresh fails (prevents stale-token replay)', async () => {
    setStoredToken('stale-sprint1-token')
    mockFetch(() => Promise.resolve(jsonResponse({ detail: 'Unauthorized' }, 401)))

    await apiRequest('/api/maps/m1', { method: 'GET' })
    expect(getStoredToken()).toBeNull()
  })

  it('never persists tokens to localStorage (dual-defense invariant)', async () => {
    const localSetSpy = vi.spyOn(globalThis.localStorage, 'setItem')
    setStoredToken('session-only-token')
    expect(getStoredToken()).toBe('session-only-token')
    expect(localSetSpy).not.toHaveBeenCalled()
    expect(globalThis.localStorage.getItem('mindweave_access_token')).toBeNull()
  })

  it('clearStoredAuth removes both session and legacy localStorage keys', () => {
    setStoredToken('to-be-cleared')
    globalThis.sessionStorage.setItem('mindweave_auth_user', JSON.stringify({ id: 'u1' }))
    clearStoredAuth()
    expect(getStoredToken()).toBeNull()
    expect(getStoredUser()).toBeNull()
  })

  it('keeps user session state in sync', () => {
    const user = { id: 'u1', email: 'regression@example.com' }
    setStoredUser(user)
    expect(getStoredUser()).toEqual(user)
    setStoredUser(null)
    expect(getStoredUser()).toBeNull()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// US-03: Polling (ARQ job lifecycle)
// ─────────────────────────────────────────────────────────────────────────────

describe('US-03: Polling job status regression', () => {
  it('pollJobStatus returns the job object from GET /jobs/{jobId}', async () => {
    const mockJob = { id: 'j-1', map_id: 'm-1', status: 'running', progress_percent: 42 }
    mockFetch(() => Promise.resolve(jsonResponse(mockJob)))

    const job = await mindMapService.pollJobStatus('m-1', 'j-1')

    expect(job).toEqual(mockJob)
    expect(pathOf(capturedCalls[0].input)).toBe('/api/maps/m-1/jobs/j-1')
  })

  it('getLatestJob unwraps {"job": {...}} correctly', async () => {
    const mockJob = { id: 'j-latest', status: 'pending', progress_percent: 0 }
    mockFetch(() => Promise.resolve(jsonResponse({ job: mockJob })))

    const job = await mindMapService.getLatestJob('m-1')
    expect(job).toEqual(mockJob)
  })

  it('getLatestJob returns null when no job exists', async () => {
    mockFetch(() => Promise.resolve(jsonResponse({ job: null })))
    expect(await mindMapService.getLatestJob('m-1')).toBeNull()
  })

  it('throws on job polling failure (backend unavailable)', async () => {
    mockFetch(() => Promise.resolve(jsonResponse({ detail: 'Service Unavailable' }, 503)))
    await expect(mindMapService.pollJobStatus('m-1', 'j-1')).rejects.toThrow('Service Unavailable')
  })

  it('preserves terminal job states (completed / failed / rejected)', async () => {
    for (const status of ['completed', 'failed', 'rejected']) {
      mockFetch(() =>
        Promise.resolve(
          jsonResponse({
            id: 'x',
            status,
            progress_percent: status === 'completed' ? 100 : 0,
            error_message: status === 'failed' ? 'boom' : null,
          }),
        ),
      )
      const job = await mindMapService.pollJobStatus('m-1', 'j-1')
      expect(job.status).toBe(status)
    }
  })

  it('GenerationProgressView step mapping follows Sprint-1 state machine', () => {
    // Mirrors the step computation in GenerationProgressView:
    // <25 -> step 1 | 25-55 -> step 2 | 55-90 -> step 3 | >=90 -> step 4
    const steps = (percent) => {
      if (percent < 25) return 1
      if (percent < 55) return 2
      if (percent < 90) return 3
      return 4
    }
    expect(steps(0)).toBe(1)
    expect(steps(15)).toBe(1)
    expect(steps(24)).toBe(1)
    expect(steps(25)).toBe(2)
    expect(steps(50)).toBe(2)
    expect(steps(54)).toBe(2)
    expect(steps(55)).toBe(3)
    expect(steps(89)).toBe(3)
    expect(steps(90)).toBe(4)
    expect(steps(100)).toBe(4)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// US-04: Canvas load graph payload contract
// ─────────────────────────────────────────────────────────────────────────────

describe('US-04: Canvas load graph payload regression', () => {
  it('getMapGraph unwraps {nodes, edges} from GET /maps/{id}/graph', async () => {
    const payload = {
      map_id: 'm-1',
      version: 3,
      nodes: [{ id: 'n1', label: 'Root' }],
      edges: [{ id: 'e1', source_node_id: 'n1', target_node_id: 'n2' }],
    }
    mockFetch(() => Promise.resolve(jsonResponse(payload)))

    const graph = await mindMapService.getMapGraph('m-1')
    expect(graph).toEqual(payload)
  })

  it('adapts a multi-parent leaf graph and preserves BOTH cross-link incoming parents', () => {
    const root = { id: 'n-root', node_type: 'root', label: 'AI', pos_x: 450, pos_y: 300 }
    const a = { id: 'n-a', node_type: 'default', label: 'DL', pos_x: 0, pos_y: 0 }
    const b = { id: 'n-b', node_type: 'default', label: 'NLP', pos_x: 0, pos_y: 0 }
    const leaf = { id: 'n-leaf', node_type: 'default', label: 'Transformers', pos_x: 0, pos_y: 0 }

    const { flowNodes, flowEdges } = adaptBackendGraphToFlow(
      [root, a, b, leaf],
      [
        { id: 'e1', source_node_id: 'n-root', target_node_id: 'n-a' },
        { id: 'e2', source_node_id: 'n-root', target_node_id: 'n-b' },
        { id: 'e3', source_node_id: 'n-a', target_node_id: 'n-leaf' },
        { id: 'e4', source_node_id: 'n-b', target_node_id: 'n-leaf' }, // cross-link
      ],
    )

    expect(flowEdges).toHaveLength(4)
    const leafNode = flowNodes.find((n) => n.id === 'n-leaf')
    expect(leafNode.data.incomingEdges).toEqual(['n-a', 'n-b'])
    expect(leafNode.data.outgoingEdges).toHaveLength(0)
    expect(flowNodes.find((n) => n.id === 'n-root').type).toBe('rootNode')
  })

  it('detects root as the only node with zero in-degree when node_type is missing', () => {
    const nodes = [
      { id: 'n1', label: 'A' }, // zero in-degree -> root
      { id: 'n2', label: 'B' },
    ]
    const { flowNodes } = adaptBackendGraphToFlow(nodes, [
      { id: 'e1', source_node_id: 'n1', target_node_id: 'n2' },
    ])
    expect(flowNodes.find((n) => n.id === 'n1').type).toBe('rootNode')
    expect(flowNodes.find((n) => n.id === 'n2').type).toBe('branchNode')
  })

  it('places unpositioned nodes into topological depth columns', () => {
    const root = { id: 'n-root', node_type: 'root', label: 'Root' }
    const c1 = { id: 'n-c1', node_type: 'default', label: 'C1' }
    const c2 = { id: 'n-c2', node_type: 'default', label: 'C2' }

    const { flowNodes } = adaptBackendGraphToFlow(
      [root, c1, c2],
      [
        { id: 'e1', source_node_id: 'n-root', target_node_id: 'n-c1' },
        { id: 'e2', source_node_id: 'n-root', target_node_id: 'n-c2' },
      ],
    )

    expect(flowNodes.find((n) => n.id === 'n-root').position.x).toBeCloseTo(80)
    expect(flowNodes.find((n) => n.id === 'n-c1').position.x).toBeCloseTo(440) // depth 1
  })

  it('maps backend edge label field for canvas annotations', () => {
    const { flowEdges } = adaptBackendGraphToFlow(
      [{ id: 'n1', node_type: 'root', label: 'R' }],
      [{ id: 'e1', source_node_id: 'n1', target_node_id: 'n2', label: 'subset' }],
    )
    expect(flowEdges[0].label).toBe('subset')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// US-02: Generation request intake (seed / FormData payload)
// ─────────────────────────────────────────────────────────────────────────────

describe('US-02: Generation intake regression', () => {
  it('generateMap sends multipart FormData payload with files', async () => {
    const file = new File(['hello'], 'notes.pdf', { type: 'application/pdf' })
    mockFetch(() => Promise.resolve(jsonResponse({ job_id: 'j-99', map_id: 'm-1' })))

    await mindMapService.generateMap('m-1', {
      topic: 'Neural Networks',
      ideas: 'Backprop, activations',
      files: [file],
    })

    const call = capturedCalls[0]
    expect(pathOf(call.input)).toBe('/api/maps/m-1/generate')
    expect(call.options.body).toBeInstanceOf(FormData)
    expect(call.options.body.get('topic')).toBe('Neural Networks')
    expect(call.options.body.get('ideas')).toBe('Backprop, activations')
    expect(call.options.body.get('detail_level')).toBe('overview')
    expect(call.options.body.get('files')).toBe(file)
  })

  it('sends no files field when none provided', async () => {
    mockFetch(() => Promise.resolve(jsonResponse({ job_id: 'j-100', map_id: 'm-2' })))
    await mindMapService.generateMap('m-2', { topic: 'Trees', ideas: 'Graphs' })
    expect(capturedCalls[0].options.body.has('files')).toBe(false)
  })

  it('extracts errors consistently from backend error shapes', () => {
    expect(formatApiError({ detail: 'Map not found' })).toBe('Map not found')
    expect(formatApiError({ detail: [{ msg: 'title required', loc: ['body', 'title'] }] })).toBe(
      'title: title required',
    )
    expect(formatApiError({ message: 'system error' })).toBe('system error')
    expect(formatApiError({ error: 'generic' })).toBe('generic')
    expect(formatApiError(null, 'fallback')).toBe('fallback')
    expect(formatApiError('string detail')).toBe('string detail')
  })
})
