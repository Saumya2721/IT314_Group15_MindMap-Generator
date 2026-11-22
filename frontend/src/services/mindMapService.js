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
   * Server-backed search of mind maps by title / description (S2-FE5 / US-08).
   * Endpoint: GET /api/maps?q=<query>&limit=<limit>
   */
  async searchMaps(query, limit = 20) {
    const qs = `?q=${encodeURIComponent((query || '').trim())}&limit=${limit}`
    const res = await apiClient.get(`/api/maps${qs}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to search mind maps')
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
   * Update map title or metadata (S2-FE5 / US-08: also persists the editor viewport).
   * Endpoint: PATCH /api/maps/{id}
   */
  async updateMap(id, { title, is_public, view_x, view_y, view_zoom }) {
    const payload = {}
    if (title !== undefined) payload.title = title.trim()
    if (is_public !== undefined) payload.is_public = is_public
    if (view_x !== undefined) payload.view_x = view_x
    if (view_y !== undefined) payload.view_y = view_y
    if (view_zoom !== undefined) payload.view_zoom = view_zoom

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
  async quickGenerate({
    topic,
    ideas,
    detail_level = 'overview',
    purpose = 'general',
    map_id = null,
    seed_url = null,
    seedUrl = null,
  }) {
    const effectiveSeedUrl = (seed_url || seedUrl || '').trim() || undefined
    const payload = {
      topic: topic?.trim() || undefined,
      ideas: ideas?.trim() || undefined,
      detail_level,
      purpose,
      map_id: map_id || undefined,
      ...(effectiveSeedUrl ? { seed_url: effectiveSeedUrl } : {}),
    }

    const res = await apiClient.post('/api/maps/quick-generate', payload)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to generate mind map')
    }
    return res.data
  },

  /**
   * Multipart/form-data generation supporting text + file uploads + seed URL (S3-FE4)
   * Endpoint: POST /api/maps/{map_id}/generate
   */
  async generateMap(
    mapId,
    {
      topic,
      ideas,
      detail_level = 'overview',
      purpose = 'general',
      files = [],
      seed_url = null,
      seedUrl = null,
    },
  ) {
    const formData = new FormData()
    if (topic) formData.append('topic', topic.trim())
    if (ideas) formData.append('ideas', ideas.trim())
    if (detail_level) formData.append('detail_level', detail_level)
    if (purpose) formData.append('purpose', purpose)

    const effectiveSeedUrl = (seed_url || seedUrl || '').trim()
    if (effectiveSeedUrl) {
      formData.append('seed_url', effectiveSeedUrl)
    }

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
  async updateMapGraph(mapId, { nodes, edges, expected_version }) {
    const payload = { nodes, edges }
    if (expected_version !== undefined && expected_version !== null) {
      payload.expected_version = expected_version
    }
    const res = await apiClient.patch(`/api/maps/${mapId}/graph`, payload)
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
   * Partial branch regeneration rooted at nodeId (S2-FE3 / S2-BE2)
   * Endpoint: POST /api/maps/{map_id}/nodes/{node_id}/regenerate
   */
  async regenerateBranch(mapId, nodeId, { instruction, detail_level } = {}) {
    const res = await apiClient.post(`/api/maps/${mapId}/nodes/${nodeId}/regenerate`, {
      instruction: instruction ? instruction.trim() : undefined,
      detail_level: detail_level || undefined,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to initiate branch regeneration')
    }
    return res.data
  },

  /**
   * Fetch comments on a node (S2-FE3 / S2-BE2)
   * Endpoint: GET /api/maps/{map_id}/nodes/{node_id}/comments
   */
  async getNodeComments(mapId, nodeId) {
    const res = await apiClient.get(`/api/maps/${mapId}/nodes/${nodeId}/comments`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to fetch node comments')
    }
    return res.data
  },

  /**
   * Create a new comment on a node (S2-FE3 / S2-BE2)
   * Endpoint: POST /api/maps/{map_id}/nodes/{node_id}/comments
   */
  async createNodeComment(mapId, nodeId, content) {
    const res = await apiClient.post(`/api/maps/${mapId}/nodes/${nodeId}/comments`, {
      content: content.trim(),
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to create comment')
    }
    return res.data
  },

  /**
   * Update an existing comment (e.g. resolve toggle or edit text)
   * Endpoint: PATCH /api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}
   */
  async updateNodeComment(mapId, nodeId, commentId, { content, resolved, status }) {
    const payload = {}
    if (content !== undefined) payload.content = content.trim()
    if (status !== undefined) payload.status = status
    else if (resolved !== undefined) payload.status = resolved ? 'resolved' : 'open'
    const res = await apiClient.patch(
      `/api/maps/${mapId}/nodes/${nodeId}/comments/${commentId}`,
      payload,
    )
    if (!res.ok) {
      throw new Error(res.error || 'Failed to update comment')
    }
    return res.data
  },

  /**
   * Delete a comment on a node (S2-FE3 / S2-BE2)
   * Endpoint: DELETE /api/maps/{map_id}/nodes/{node_id}/comments/{comment_id}
   */
  async deleteNodeComment(mapId, nodeId, commentId) {
    const res = await apiClient.delete(`/api/maps/${mapId}/nodes/${nodeId}/comments/${commentId}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to delete comment')
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
      nodes_count: s.nodes_count ?? s.snapshot_data?.nodes?.length ?? 0,
      edges_count: s.edges_count ?? s.snapshot_data?.edges?.length ?? 0,
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

  /**
   * Send a chat message (US-14 / S2-FE4)
   * Endpoint: POST /api/chat/messages
   */
  async sendChatMessage(mapId, content, proposedAction = null, targetNodeId = null) {
    const payload = {
      map_id: mapId,
      content,
      proposed_action: proposedAction,
    }
    if (targetNodeId) {
      payload.target_node_id = targetNodeId
    }
    const res = await apiClient.post('/api/chat/messages', payload)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to send message')
    }
    return res.data
  },

  /**
   * Confirm or reject a proposed action (US-14 / S2-FE4)
   * Endpoint: POST /api/chat/actions/{message_id}/confirm
   */
  async confirmProposedAction(messageId, isConfirmed) {
    const res = await apiClient.post(`/api/chat/actions/${messageId}/confirm`, {
      is_confirmed: isConfirmed,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to confirm action')
    }
    return res.data
  },

  /**
   * Create a node manually
   * Endpoint: POST /api/maps/{map_id}/nodes
   */
  async createNode(
    mapId,
    {
      label,
      node_type = 'concept',
      description = '',
      pos_x = 0,
      pos_y = 0,
      background_color,
      text_color,
      parent_node_id,
      edge_label,
    },
  ) {
    const res = await apiClient.post(`/api/maps/${mapId}/nodes`, {
      label,
      node_type,
      description,
      pos_x,
      pos_y,
      background_color,
      text_color,
      parent_node_id,
      edge_label,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to create node')
    }
    return res.data
  },

  /**
   * Create an edge manually
   * Endpoint: POST /api/maps/{map_id}/edges
   */
  async createEdge(mapId, { source_node_id, target_node_id, label = '' }) {
    const res = await apiClient.post(`/api/maps/${mapId}/edges`, {
      source_node_id,
      target_node_id,
      label,
    })
    if (!res.ok) {
      throw new Error(res.error || 'Failed to create edge')
    }
    return res.data
  },

  /**
   * Update an edge label or properties
   * Endpoint: PATCH /api/maps/{map_id}/edges/{edge_id}
   */
  async updateEdge(mapId, edgeId, updates) {
    const res = await apiClient.patch(`/api/maps/${mapId}/edges/${edgeId}`, updates)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to update edge')
    }
    return res.data
  },

  /**
   * Delete an edge
   * Endpoint: DELETE /api/maps/{map_id}/edges/{edge_id}
   */
  async deleteEdge(mapId, edgeId) {
    const res = await apiClient.delete(`/api/maps/${mapId}/edges/${edgeId}`)
    if (!res.ok) {
      throw new Error(res.error || 'Failed to delete edge')
    }
    return res.data
  },
}
