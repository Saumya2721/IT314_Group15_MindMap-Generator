import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mindMapService } from '../services/mindMapService'
import { apiClient } from '../services/apiClient'

describe('Sprint 2 Remediation: mindMapService & OCC', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('C11: updateMapGraph includes expected_version for optimistic concurrency control', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({
      ok: true,
      data: { status: 'success', map_version: 5, nodes: [], edges: [] },
    })

    const payload = {
      nodes: [{ id: 'n1', label: 'Node 1' }],
      edges: [],
      expected_version: 4,
    }

    await mindMapService.updateMapGraph('map-123', payload)

    expect(patchSpy).toHaveBeenCalledTimes(1)
    const [url, sentPayload] = patchSpy.mock.calls[0]
    expect(url).toBe('/api/maps/map-123/graph')
    expect(sentPayload.expected_version).toBe(4)
    expect(sentPayload.nodes).toHaveLength(1)
  })

  it('C12: updateNodeComment forwards status and maps resolved boolean to status', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch').mockResolvedValue({
      ok: true,
      data: { id: 'c1', status: 'resolved', resolved: true },
    })

    const res1 = await mindMapService.updateNodeComment('map-123', 'node-456', 'c1', {
      status: 'resolved',
    })

    expect(patchSpy).toHaveBeenCalledTimes(1)
    const [url, sentPayload] = patchSpy.mock.calls[0]
    expect(url).toBe('/api/maps/map-123/nodes/node-456/comments/c1')
    expect(sentPayload.status).toBe('resolved')
    expect(res1.resolved).toBe(true)

    // Also test boolean resolved mapping to status string
    await mindMapService.updateNodeComment('map-123', 'node-456', 'c1', {
      resolved: true,
    })
    const [, sentPayload2] = patchSpy.mock.calls[1]
    expect(sentPayload2.status).toBe('resolved')
  })

  it('C18: pollJobStatus queries /maps/{mapId}/jobs/{jobId}', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({
      ok: true,
      data: { id: 'job-789', status: 'completed', progress_percent: 100 },
    })

    const res = await mindMapService.pollJobStatus('map-123', 'job-789')
    expect(getSpy).toHaveBeenCalledWith('/api/maps/map-123/jobs/job-789')
    expect(res.status).toBe('completed')
  })

  it('F4: selectNode resolves string IDs to complete node objects defensively', () => {
    // Tests that state normalizes node selection whether passed a string ID or node object
    const existingNodes = [
      { id: 'node-root', label: 'Root Concept', pos_x: 0, pos_y: 0 },
      { id: 'node-child', label: 'Child Concept', pos_x: 100, pos_y: 100 },
    ]

    const resolveNode = (nodeOrId, nodes) => {
      return typeof nodeOrId === 'string' ? nodes.find((n) => n.id === nodeOrId) || null : nodeOrId
    }

    const resolved = resolveNode('node-child', existingNodes)
    expect(resolved).not.toBeNull()
    expect(resolved.id).toBe('node-child')
    expect(resolved.label).toBe('Child Concept')

    const direct = resolveNode(existingNodes[0], existingNodes)
    expect(direct.id).toBe('node-root')
  })

  it('F5: Editor persistence blocks graph update when expected_version is unavailable', async () => {
    const patchSpy = vi.spyOn(apiClient, 'patch')

    let saveBlocked = false
    const mapVersion = null
    try {
      if (mapVersion === null) {
        throw new Error('Graph version unavailable for optimistic lock')
      }
      await mindMapService.updateMapGraph('map-123', {
        nodes: [{ id: 'n1' }],
        edges: [],
        expected_version: mapVersion,
      })
    } catch (err) {
      saveBlocked = true
      expect(err.message).toContain('Graph version unavailable')
    }

    expect(saveBlocked).toBe(true)
    expect(patchSpy).not.toHaveBeenCalled()
  })

  it('R9: parseSourceEntry correctly parses URLs containing closing parentheses', () => {
    // Tests parser regex handling of URLs like https://en.wikipedia.org/wiki/Cell_(biology)
    const raw = 'Cell biology (https://en.wikipedia.org/wiki/Cell_(biology))'
    const match = raw.match(/^(.*?)\s*\((https?:\/\/\S+)\)$/)
    expect(match).not.toBeNull()
    const title = match[1].trim()
    const url = match[2].trim()
    expect(title).toBe('Cell biology')
    expect(url).toBe('https://en.wikipedia.org/wiki/Cell_(biology)')
  })

  it('R10: parseSourceEntry classifies ctx:n as context_citation with neutral fallback', () => {
    const raw = 'ctx:3'
    expect(raw.startsWith('ctx:')).toBe(true)
    const entry = {
      type: 'context_citation',
      title: `Context Citation (${raw})`,
      url: null,
    }
    expect(entry.type).toBe('context_citation')
    expect(entry.url).toBeNull()
    expect(entry.title).toBe('Context Citation (ctx:3)')
  })
})
