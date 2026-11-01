/**
 * Topological visual layout calculator for MindMap nodes
 */

export function transformBackendGraph(rawNodes = [], rawEdges = []) {
  if (!rawNodes || rawNodes.length === 0) {
    return []
  }

  // Build parent lookup from edges: target -> source
  const parentLookup = new Map()
  for (const edge of rawEdges || []) {
    const sourceId = edge.source_node_id || edge.source
    const targetId = edge.target_node_id || edge.target
    if (sourceId && targetId) {
      parentLookup.set(String(targetId), String(sourceId))
    }
  }

  // Find root node (node_type === 'root' or no parent)
  let rootNode = rawNodes.find((n) => n.node_type === 'root')
  if (!rootNode && rawNodes.length > 0) {
    rootNode = rawNodes.find((n) => !parentLookup.has(String(n.id))) || rawNodes[0]
  }
  const rootId = rootNode ? String(rootNode.id) : null

  // Track children for layout calculation
  const childrenMap = new Map()
  for (const n of rawNodes) {
    const nid = String(n.id)
    const pid = parentLookup.get(nid) || (nid !== rootId ? rootId : null)
    if (pid) {
      if (!childrenMap.has(pid)) childrenMap.set(pid, [])
      childrenMap.get(pid).push(nid)
    }
  }

  // Check if coordinates need initial distribution
  const allZero = rawNodes.every((n) => (n.pos_x === 0 || !n.pos_x) && (n.pos_y === 0 || !n.pos_y))

  return rawNodes.map((n, idx) => {
    const id = String(n.id)
    const parentId = parentLookup.get(id) || (id !== rootId ? rootId : null)
    const isRoot = id === rootId || n.node_type === 'root'
    const isBranch = !isRoot && (parentId === rootId || n.node_type === 'branch')
    const type = isRoot ? 'core' : isBranch ? 'branch' : 'leaf'

    // Calculate layout position if coordinates are not provided by backend
    let x = Number(n.pos_x) || 0
    let y = Number(n.pos_y) || 0

    if (allZero) {
      if (isRoot) {
        x = 80
        y = 280
      } else if (isBranch) {
        const branchIndex = (childrenMap.get(rootId) || []).indexOf(id)
        const bIdx = branchIndex >= 0 ? branchIndex : idx
        x = 480
        y = 120 + bIdx * 180
      } else {
        const siblings = parentId ? childrenMap.get(parentId) || [] : []
        const sIdx = siblings.indexOf(id)
        const parentNode = rawNodes.find((p) => String(p.id) === parentId)
        const py = parentNode ? Number(parentNode.pos_y) || 280 : 280
        x = 860
        y = py - 40 + (sIdx >= 0 ? sIdx * 90 : idx * 60)
      }
    }

    const defaultTint = isRoot ? '#155E63' : isBranch ? '#C96B4B' : '#899B72'
    const tint = n.background_color || defaultTint

    return {
      id,
      type,
      parentId,
      title: n.label || 'Untitled Concept',
      subtitle: n.description || '',
      category: n.node_data?.category || (isRoot ? 'Core Focus' : isBranch ? 'Branch' : 'Topic'),
      x,
      y,
      tint,
    }
  })
}
