/**
 * Unit tests for S3-FE2 Long-Term Memory Management & GDPR Export.
 * Tests memoryService contract alignment against backend /api/memory routes.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { memoryService } from '../services/memoryService'
import { apiClient } from '../services/apiClient'

vi.mock('../services/apiClient', () => ({
  apiClient: {
    get: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

describe('S3-FE2: Memory Service Operations', () => {
  const sampleBackendMemories = [
    {
      id: 'mem-1',
      user_id: 'usr-1',
      kind: 'preference',
      memory_type: 'preference',
      content: 'Prefers deep scientific domain terminology.',
      importance: 0.9,
      is_active: true,
      map_id: null,
      scope: 'global',
      created_at: '2026-11-20T10:00:00Z',
    },
    {
      id: 'mem-2',
      user_id: 'usr-1',
      kind: 'decision',
      memory_type: 'decision',
      content: 'Excludes introductory overview nodes.',
      importance: 0.8,
      is_active: true,
      map_id: 'map-123',
      scope: 'map',
      created_at: '2026-11-21T10:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    apiClient.get.mockImplementation(async (url) => {
      if (url.includes('/api/memory/export')) {
        return {
          ok: true,
          data: {
            user_id: 'usr-1',
            exported_at: '2026-11-23T12:00:00Z',
            total_memories: 2,
            memories: sampleBackendMemories,
          },
        }
      }
      return {
        ok: true,
        data: {
          items: sampleBackendMemories,
          total: 2,
          limit: 100,
          offset: 0,
        },
      }
    })
    apiClient.patch.mockResolvedValue({
      ok: true,
      data: { success: true },
    })
    apiClient.delete.mockResolvedValue({
      ok: true,
      data: { success: true },
    })
  })

  it('retrieves memories with correct privacy schema fields', async () => {
    const items = await memoryService.getMemories('all')
    expect(apiClient.get).toHaveBeenCalledWith('/api/memory?limit=100')
    expect(Array.isArray(items)).toBe(true)
    expect(items.length).toBe(2)

    items.forEach((m) => {
      expect(m).toHaveProperty('id')
      expect(m).toHaveProperty('kind')
      expect(m).toHaveProperty('content')
      expect(m).toHaveProperty('scope')
      expect(m).toHaveProperty('is_active')
      expect(['preference', 'decision', 'fact']).toContain(m.kind)
    })
  })

  it('filters memories by global and map scope', async () => {
    const all = await memoryService.getMemories('all')
    const globalItems = all.filter((m) => m.scope === 'global')
    const mapItems = all.filter((m) => m.scope === 'map')

    expect(globalItems.length).toBe(1)
    expect(mapItems.length).toBe(1)
    expect(globalItems[0].scope).toBe('global')
    expect(mapItems[0].scope).toBe('map')
    expect(mapItems[0].map_id).toBe('map-123')
  })

  it('handles memory content inline updates', async () => {
    const updated = await memoryService.updateMemoryContent('mem-1', 'Updated steering preference')
    expect(apiClient.patch).toHaveBeenCalledWith('/api/memory/mem-1', {
      content: 'Updated steering preference',
    })
    expect(updated).toBeDefined()
    expect(updated.success).toBe(true)
  })

  it('toggles memory active/disabled state', async () => {
    const toggled = await memoryService.toggleMemoryActive('mem-1', false)
    expect(apiClient.patch).toHaveBeenCalledWith('/api/memory/mem-1', {
      is_active: false,
    })
    expect(toggled).toBeDefined()
    expect(toggled.is_active).toBe(false)
  })

  it('deletes memory record', async () => {
    const res = await memoryService.deleteMemory('mem-1')
    expect(apiClient.delete).toHaveBeenCalledWith('/api/memory/mem-1')
    expect(res).toBeDefined()
    expect(res.success).toBe(true)
  })

  it('generates GDPR-compliant export bundle for data portability', async () => {
    const bundle = await memoryService.exportAllMemories()
    expect(apiClient.get).toHaveBeenCalledWith('/api/memory/export')
    expect(bundle).toBeDefined()
    expect(bundle).toHaveProperty('export_standard')
    expect(bundle).toHaveProperty('exported_at')
    expect(bundle).toHaveProperty('memories')
    expect(Array.isArray(bundle.memories)).toBe(true)
    expect(bundle.memories.length).toBe(2)
  })
})
