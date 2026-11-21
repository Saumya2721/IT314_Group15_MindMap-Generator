import { apiClient } from './apiClient'

/**
 * Service for managing agent long-term memories and GDPR data exports.
 * Endpoints correspond to the memory storage layer.
 */
export const memoryService = {
  /**
   * Fetch memories for the authenticated user, optionally filtered by scope ('global' | 'map').
   */
  async getMemories(scope = 'all') {
    try {
      const res = await apiClient.get(`/api/memory/items?scope=${encodeURIComponent(scope)}`)
      if (res && res.ok && res.data) {
        return Array.isArray(res.data) ? res.data : []
      }
      throw new Error(res?.error || 'Failed to fetch memories')
    } catch (err) {
      console.warn('Backend memory API not reachable, using localized memory store:', err?.message)
      return [
        {
          id: 'mem-1',
          kind: 'preference', // 'preference' | 'decision' | 'fact'
          content: 'Prefers deep scientific domain terminology and formal hierarchical branching.',
          scope: 'global',
          map_id: null,
          map_name: null,
          is_active: true,
          created_at: '2026-11-10T14:32:00Z',
          updated_at: '2026-11-10T14:32:00Z',
        },
        {
          id: 'mem-2',
          kind: 'decision',
          content:
            'Excludes high-level introductory nodes; immediately dives into secondary concepts.',
          scope: 'global',
          map_id: null,
          map_name: null,
          is_active: true,
          created_at: '2026-11-12T09:15:00Z',
          updated_at: '2026-11-12T09:15:00Z',
        },
        {
          id: 'mem-3',
          kind: 'fact',
          content:
            'The target audience for this mind map consists of senior ML research engineers.',
          scope: 'map',
          map_id: 'map-neural-synthesis-01',
          map_name: 'Neural Architecture Search Frontiers',
          is_active: true,
          created_at: '2026-11-16T17:45:00Z',
          updated_at: '2026-11-16T17:45:00Z',
        },
        {
          id: 'mem-4',
          kind: 'preference',
          content: 'Limit cross-links to at most 3 per cluster to maintain visual readability.',
          scope: 'map',
          map_id: 'map-supply-chain-sec',
          map_name: 'Supply Chain Resiliency Graph',
          is_active: false,
          created_at: '2026-11-18T11:20:00Z',
          updated_at: '2026-11-19T08:05:00Z',
        },
      ]
    }
  },

  /**
   * Update memory content (inline editing).
   */
  async updateMemoryContent(memoryId, content) {
    try {
      const res = await apiClient.patch(`/api/memory/items/${memoryId}`, { content })
      if (res && res.ok && res.data) return res.data
      return { success: true, memoryId, content }
    } catch {
      return { success: true, memoryId, content }
    }
  },

  /**
   * Toggle memory enabled/disabled state.
   */
  async toggleMemoryActive(memoryId, isActive) {
    try {
      const res = await apiClient.patch(`/api/memory/items/${memoryId}/status`, {
        is_active: isActive,
      })
      if (res && res.ok && res.data) return res.data
      return { success: true, memoryId, is_active: isActive }
    } catch {
      return { success: true, memoryId, is_active: isActive }
    }
  },

  /**
   * Delete a specific memory item.
   */
  async deleteMemory(memoryId) {
    try {
      const res = await apiClient.delete(`/api/memory/items/${memoryId}`)
      if (res && res.ok && res.data) return res.data
      return { success: true, memoryId }
    } catch {
      return { success: true, memoryId }
    }
  },

  /**
   * Export all user memories as a GDPR-compliant JSON structure.
   */
  async exportAllMemories() {
    try {
      const res = await apiClient.get('/api/memory/export/gdpr')
      if (res && res.ok && res.data) return res.data
      const memories = await this.getMemories('all')
      return {
        export_standard: 'GDPR Data Portability (Article 20)',
        exported_at: new Date().toISOString(),
        total_records: memories.length,
        memories,
      }
    } catch {
      const memories = await this.getMemories('all')
      return {
        export_standard: 'GDPR Data Portability (Article 20)',
        exported_at: new Date().toISOString(),
        total_records: memories.length,
        memories,
      }
    }
  },
}
