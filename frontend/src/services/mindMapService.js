import { apiClient } from './apiClient'
import { formatRelativeTime } from '../utils/formatters'

export const mindMapService = {
  /**
   * Fetch all mind maps belonging to the authenticated user
   * Endpoint: GET /api/maps
   */
  async getMaps() {
    const res = await apiClient.get('/api/maps')
    if (!res.ok) {
      throw new Error(res.error || 'Failed to fetch mind maps')
    }

    const maps = res.data || []
    return maps.map((m) => ({
      ...m,
      lastEdited: formatRelativeTime(m.updated_at || m.created_at),
      bookmarked: false,
    }))
  },

  /**
   * Fetch a single mind map by ID
   * Endpoint: GET /api/maps/{id}
   */
  async getMapById(id) {
    const res = await apiClient.get(`/api/maps/${id}`)
    if (!res.ok) {
      throw new Error(res.error || `Map ${id} not found`)
    }

    const m = res.data
    return {
      ...m,
      lastEdited: formatRelativeTime(m.updated_at || m.created_at),
    }
  },

  /**
   * Create a new blank mind map
   * Endpoint: POST /api/maps
   */
  async createMap({ title = 'Untitled mind map' }) {
    const res = await apiClient.post('/api/maps', { title: title.trim() })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to create mind map')
    }

    const m = res.data
    return {
      ...m,
      lastEdited: 'Just now',
      bookmarked: false,
    }
  },

  /**
   * Update map title or metadata
   * Endpoint: PATCH /api/maps/{id}
   */
  async updateMap(id, { title, is_public }) {
    const payload = {}
    if (title !== undefined) payload.title = title.trim()
    if (is_public !== undefined) payload.is_public = is_public

    const res = await apiClient.patch(`/api/maps/${id}`, payload)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to update mind map')
    }
    return res.data
  },

  /**
   * Delete a mind map
   * Endpoint: DELETE /api/maps/{id}
   */
  async deleteMap(id) {
    const res = await apiClient.delete(`/api/maps/${id}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to delete mind map')
    }
    return res.data
  },

  /**
   * Direct quick generation (synchronous or queued)
   * Endpoint: POST /api/maps/quick-generate
   */
  async quickGenerate({ topic, ideas, detail_level = 'overview', map_id = null }) {
    const payload = {
      topic: topic?.trim() || undefined,
      ideas: ideas?.trim() || undefined,
      detail_level,
      map_id: map_id || undefined,
    }

    const res = await apiClient.post('/api/maps/quick-generate', payload)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to generate mind map')
    }
    return res.data
  },

  /**
   * Multipart/form-data generation supporting text + file uploads
   * Endpoint: POST /api/maps/{map_id}/generate
   */
  async generateMap(mapId, { topic, ideas, detail_level = 'overview', files = [] }) {
    const formData = new FormData()
    if (topic) formData.append('topic', topic.trim())
    if (ideas) formData.append('ideas', ideas.trim())
    if (detail_level) formData.append('detail_level', detail_level)

    if (files && files.length > 0) {
      for (const f of files) {
        if (f instanceof File) {
          formData.append('files', f)
        }
      }
    }

    const res = await apiClient.post(`/api/maps/${mapId}/generate`, formData)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to initiate map generation')
    }
    return res.data
  },

  /**
   * Poll generation job status
   * Endpoint: GET /api/maps/{map_id}/jobs/{job_id}
   */
  async pollJobStatus(mapId, jobId) {
    const res = await apiClient.get(`/api/maps/${mapId}/jobs/${jobId}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to check job progress')
    }
    return res.data
  },

  /**
   * Get latest job for a map
   * Endpoint: GET /api/maps/{map_id}/jobs/latest
   */
  async getLatestJob(mapId) {
    const res = await apiClient.get(`/api/maps/${mapId}/jobs/latest`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to query latest job')
    }
    return res.data?.job || null
  },

  /**
   * Fetch full graph of nodes and edges
   * Endpoint: GET /api/maps/{map_id}/graph
   */
  async getMapGraph(mapId) {
    const res = await apiClient.get(`/api/maps/${mapId}/graph`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to load mind map graph')
    }
    return res.data // { nodes: [...], edges: [...] }
  },

  /**
   * Persist updated graph (nodes & edges) to backend
   * Endpoint: PATCH /api/maps/{map_id}/graph
   */
  async updateMapGraph(mapId, { nodes, edges }) {
    const res = await apiClient.patch(`/api/maps/${mapId}/graph`, {
      nodes,
      edges,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to update mind map graph')
    }
    return res.data
  },

  /**
   * Full map regeneration from natural language instruction
   * Endpoint: POST /api/maps/{map_id}/regenerate
   */
  async regenerateMap(mapId, { instruction, detail_level = 'overview' }) {
    const res = await apiClient.post(`/api/maps/${mapId}/regenerate`, {
      instruction: instruction.trim(),
      detail_level,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to initiate map regeneration')
    }
    return res.data
  },

  /**
   * Get chat history for map
   * Endpoint: GET /api/maps/{map_id}/chat
   */
  async getChatHistory(mapId) {
    const res = await apiClient.get(`/api/maps/${mapId}/chat`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to load conversation history')
    }
    return res.data
  },

  // ------------------------------------------------------------------
  // Version History / Snapshots (US-04 / S2-FE7)
  // ------------------------------------------------------------------

  /**
   * Fetch all snapshots for a map, newest first
   * Endpoint: GET /api/maps/{map_id}/snapshots
   */
  async getSnapshots(mapId) {
    const res = await apiClient.get(`/api/maps/${mapId}/snapshots`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to fetch snapshots')
    }

    const raw = res.data || []
    return raw.map((s) => ({
      id: s.id,
      map_id: s.map_id,
      version_number: s.version_number,
      trigger_source: s.trigger_source,
      trigger_job_id: s.trigger_job_id || null,
      label: s.label || `Version ${s.version_number}`,
      snapshot_data: s.snapshot_data || { nodes: [], edges: [] },
      created_by: s.created_by || null,
      created_at: s.created_at || null,
    }))
  },

  /**
   * Fetch a single snapshot by ID (includes snapshot_data payload)
   * Endpoint: GET /api/maps/{map_id}/snapshots/{snapshot_id}
   */
  async getSnapshotDetail(mapId, snapshotId) {
    const res = await apiClient.get(`/api/maps/${mapId}/snapshots/${snapshotId}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to fetch snapshot')
    }
    const s = res.data
    if (!s) throw new Error('Snapshot not found')
    return {
      ...s,
      snapshot_data: s.snapshot_data || { nodes: [], edges: [] },
    }
  },

  /**
   * Restore a map to a previous snapshot
   * Endpoint: POST /api/maps/{map_id}/snapshots/{snapshot_id}/restore
   */
  async restoreSnapshot(mapId, snapshotId) {
    const res = await apiClient.post(`/api/maps/${mapId}/snapshots/${snapshotId}/restore`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to restore snapshot')
    }
    return res.data // { snapshot_id, snapshot_data: { nodes, edges } }
  },

  /**
   * Delete a snapshot
   * Endpoint: DELETE /api/maps/{map_id}/snapshots/{snapshot_id}
   */
  async deleteSnapshot(mapId, snapshotId) {
    const res = await apiClient.delete(`/api/maps/${mapId}/snapshots/${snapshotId}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to delete snapshot')
    }
    return res.data
  },
}
