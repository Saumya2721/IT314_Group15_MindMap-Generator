/**
 * Unit tests for S3-FE2 Long-Term Memory Management & GDPR Export.
 * Plain Vitest Node environment test suite without browser dependencies.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { memoryService } from '../services/memoryService'

describe('S3-FE2: Memory Service Operations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('retrieves memories with correct privacy schema fields', async () => {
    const items = await memoryService.getMemories('all')
    expect(Array.isArray(items)).toBe(true)
    expect(items.length).toBeGreaterThan(0)

    items.forEach((m) => {
      expect(m).toHaveProperty('id')
      expect(m).toHaveProperty('kind')
      expect(m).toHaveProperty('content')
      expect(m).toHaveProperty('scope')
      expect(m).toHaveProperty('is_active')
      // Ensure kind is one of preference, decision, fact
      expect(['preference', 'decision', 'fact']).toContain(m.kind)
    })
  })

  it('filters memories by global and map scope', async () => {
    const all = await memoryService.getMemories('all')
    const globalItems = all.filter((m) => m.scope === 'global')
    const mapItems = all.filter((m) => m.scope === 'map')

    expect(globalItems.length).toBeGreaterThan(0)
    expect(mapItems.length).toBeGreaterThan(0)

    globalItems.forEach((g) => expect(g.scope).toBe('global'))
    mapItems.forEach((m) => {
      expect(m.scope).toBe('map')
      expect(m.map_id).toBeDefined()
    })
  })

  it('handles memory content inline updates', async () => {
    const updated = await memoryService.updateMemoryContent('mem-1', 'Updated steering preference')
    expect(updated).toBeDefined()
    expect(updated.success).toBe(true)
  })

  it('toggles memory active/disabled state', async () => {
    const toggled = await memoryService.toggleMemoryActive('mem-1', false)
    expect(toggled).toBeDefined()
    expect(toggled.is_active).toBe(false)
  })

  it('deletes memory record', async () => {
    const res = await memoryService.deleteMemory('mem-1')
    expect(res).toBeDefined()
    expect(res.success).toBe(true)
  })

  it('generates GDPR-compliant export bundle for data portability', async () => {
    const bundle = await memoryService.exportAllMemories()
    expect(bundle).toBeDefined()
    expect(bundle).toHaveProperty('export_standard')
    expect(bundle).toHaveProperty('exported_at')
    expect(bundle).toHaveProperty('memories')
    expect(Array.isArray(bundle.memories)).toBe(true)
    expect(bundle.memories.length).toBeGreaterThan(0)
  })
})
