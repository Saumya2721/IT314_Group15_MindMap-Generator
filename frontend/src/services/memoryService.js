import { apiClient } from './apiClient'

/**
 * Service for managing agent long-term memories and GDPR data exports (S3-BE4 / S3-FE2).
 * Endpoints strictly correspond to the canonical backend /api/memory routes.
 */
export const memoryService = {
  /**
   * Fetch memories for the authenticated user, optionally filtered by scope ('global' | 'map').
   */
  async getMemories(scope = 'all', mapId = null) {
    let url = '/api/memory?limit=100'
    if (mapId) {
      url += `&map_id=${encodeURIComponent(mapId)}`
    }
    const items = []
    let offset = 0
    while (true) {
      const res = await apiClient.get(offset ? `${url}&offset=${offset}` : url)
      if (!res?.ok || !Array.isArray(res.data?.items)) {
        throw new Error(res?.error || 'Failed to fetch memories from server')
      }
      const page = res.data.items
      items.push(
        ...page.map((m) => ({
          ...m,
          kind: m.kind || m.memory_type || 'fact',
          scope: m.scope || (m.map_id ? 'map' : 'global'),
          is_active: Boolean(m.is_active),
        })),
      )
      offset += page.length
      if (page.length === 0 || offset >= (res.data.total ?? offset)) break
    }
    return scope === 'all' ? items : items.filter((memory) => memory.scope === scope)
  },

  /**
   * Update memory content (inline editing).
   */
  async updateMemoryContent(memoryId, content) {
    const res = await apiClient.patch(`/api/memory/${encodeURIComponent(memoryId)}`, { content })
    if (res && res.ok) {
      return { success: true, ...(res.data || {}), id: memoryId, content }
    }
    throw new Error(res?.error || 'Failed to update memory')
  },

  /**
   * Toggle memory enabled/disabled active state.
   */
  async toggleMemoryActive(memoryId, isActive) {
    const res = await apiClient.patch(`/api/memory/${encodeURIComponent(memoryId)}`, {
      is_active: isActive,
    })
    if (res && res.ok) {
      return { success: true, ...(res.data || {}), id: memoryId, is_active: isActive }
    }
    throw new Error(res?.error || 'Failed to update memory active status')
  },

  /**
   * Delete a specific memory item.
   */
  async deleteMemory(memoryId) {
    const res = await apiClient.delete(`/api/memory/${encodeURIComponent(memoryId)}`)
    if (res && res.ok) {
      return { success: true, id: memoryId }
    }
    throw new Error(res?.error || 'Failed to delete memory')
  },

  /**
   * Export all user memories as a GDPR-compliant JSON structure.
   */
  async exportAllMemories() {
    const res = await apiClient.get('/api/memory/export')
    if (res && res.ok && res.data) {
      return {
        export_standard: 'GDPR Data Portability (Article 20)',
        ...res.data,
      }
    }
    throw new Error(res?.error || 'Failed to export memories')
  },
}
