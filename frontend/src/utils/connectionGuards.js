/**
 * Connection Guards & Topological Validation Utilities
 *
 * Enforces graph integrity rules for mind map editing:
 * 1. No self-connections (source !== target)
 * 2. No duplicate connections in same direction
 * 3. No directed cycles (adding edge must preserve DAG / tree hierarchy)
 * 4. Single-root invariant (central root cannot have incoming parent connections)
 * 5. Descendant collection for safe branch deletions
 */

/**
 * Checks if connection connects a node to itself.
 */
export function isSelfConnection(sourceId, targetId) {
  if (!sourceId || !targetId) return false
  return String(sourceId) === String(targetId)
}

/**
 * Checks if a connection already exists between source and target.
 */
export function isDuplicateConnection(edges = [], sourceId, targetId) {
  if (!edges || !sourceId || !targetId) return false
  const src = String(sourceId)
  const tgt = String(targetId)

  return edges.some((e) => {
    const edgeSrc = String(e.source_node_id || e.source)
    const edgeTgt = String(e.target_node_id || e.target)
    return edgeSrc === src && edgeTgt === tgt
  })
}

/**
 * Checks if adding sourceId -> targetId would create a directed cycle.
 * If targetId can already reach sourceId through existing edges,
 * then adding sourceId -> targetId completes a cycle.
 */
export function wouldCreateCycle(edges = [], sourceId, targetId) {
  if (!sourceId || !targetId) return false
  const src = String(sourceId)
  const tgt = String(targetId)

  if (src === tgt) return true

  // Build adjacency list for forward traversal
  const adjacency = new Map()
  for (const e of edges || []) {
    const s = String(e.source_node_id || e.source)
    const t = String(e.target_node_id || e.target)
    if (!adjacency.has(s)) adjacency.set(s, [])
    adjacency.get(s).push(t)
  }

  // BFS search starting from targetId looking for sourceId
  const queue = [tgt]
  const visited = new Set([tgt])

  while (queue.length > 0) {
    const current = queue.shift()
    if (current === src) {
      return true
    }

    const neighbors = adjacency.get(current) || []
    for (const neighbor of neighbors) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor)
        queue.push(neighbor)
      }
    }
  }

  return false
}

/**
 * Checks if connection attempts to make another node the parent of the root node.
 * In a mind map, the central root is the origin and cannot have incoming links.
 */
export function isInvalidRootTarget(nodes = [], targetId) {
  if (!nodes || !targetId) return false
  const tgt = String(targetId)
  const targetNode = nodes.find((n) => String(n.id) === tgt)
  return targetNode?.node_type === 'root'
}

/**
 * Validates a proposed connection against all rules.
 * Returns { valid: boolean, reason?: string }
 */
export function validateConnection(nodes = [], edges = [], connection = {}) {
  const sourceId = connection.source_node_id || connection.source
  const targetId = connection.target_node_id || connection.target

  if (!sourceId || !targetId) {
    return { valid: false, reason: 'Invalid connection endpoints.' }
  }

  if (isSelfConnection(sourceId, targetId)) {
    return { valid: false, reason: 'A concept cannot connect to itself.' }
  }

  if (isInvalidRootTarget(nodes, targetId)) {
    return {
      valid: false,
      reason: 'The central root concept cannot have incoming connections.',
    }
  }

  if (isDuplicateConnection(edges, sourceId, targetId)) {
    return {
      valid: false,
      reason: 'A connection already exists between these concepts.',
    }
  }

  if (wouldCreateCycle(edges, sourceId, targetId)) {
    return {
      valid: false,
      reason: 'Cannot create a cyclical connection in a hierarchical mind map.',
    }
  }

  return { valid: true }
}

/**
 * Returns all descendant node IDs of a given node following directed edges.
 * Used for branch deletion confirmation and recursive cascade removal.
 */
export function getDescendantNodeIds(edges = [], rootNodeId) {
  if (!rootNodeId || !edges) return []
  const startId = String(rootNodeId)

  // Adjacency of children
  const childrenMap = new Map()
  for (const e of edges) {
    const src = String(e.source_node_id || e.source)
    const tgt = String(e.target_node_id || e.target)
    if (!childrenMap.has(src)) childrenMap.set(src, [])
    childrenMap.get(src).push(tgt)
  }

  const descendants = []
  const visited = new Set([startId])
  const queue = [startId]

  while (queue.length > 0) {
    const current = queue.shift()
    const children = childrenMap.get(current) || []
    for (const child of children) {
      if (!visited.has(child)) {
        visited.add(child)
        descendants.push(child)
        queue.push(child)
      }
    }
  }

  return descendants
}
