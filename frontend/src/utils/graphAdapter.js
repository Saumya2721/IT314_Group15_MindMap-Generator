/**
 * Graph Adapter for React Flow (@xyflow/react)
 * Maps backend topological DAGs (nodes & edges) to React Flow format.
 * Preserves cross-links without collapsing multi-parent nodes.
 */

export function adaptBackendGraphToFlow(rawNodes = [], rawEdges = [], options = {}) {
  if (!rawNodes || rawNodes.length === 0) {
    return { flowNodes: [], flowEdges: [] }
  }

  // 1. Map edges 1:1 and build adjacency graph for depth/topological sorting
  const inDegreeMap = new Map()
  const childrenMap = new Map()
  const parentsMap = new Map()

  for (const n of rawNodes) {
    const nid = String(n.id)
    inDegreeMap.set(nid, 0)
    childrenMap.set(nid, [])
    parentsMap.set(nid, [])
  }

  const flowEdges = (rawEdges || []).map((edge, idx) => {
    const source = String(edge.source_node_id || edge.source)
    const target = String(edge.target_node_id || edge.target)
    const edgeId = String(edge.id || `edge_${source}_${target}_${idx}`)

    if (inDegreeMap.has(target)) {
      inDegreeMap.set(target, (inDegreeMap.get(target) || 0) + 1)
    }
    if (childrenMap.has(source)) {
      childrenMap.get(source).push(target)
    }
    if (parentsMap.has(target)) {
      parentsMap.get(target).push(source)
    }

    // Support both field names: backend returns 'label', older versions may use 'relationship_label'
    const hasLabel = Boolean(edge.label || edge.relationship_label)

    return {
      id: edgeId,
      source,
      target,
      label: edge.label || edge.relationship_label || undefined,
      type: 'smoothstep',
      animated: false,
      style: {
        stroke: '#155E63',
        strokeWidth: hasLabel ? 2 : 1.5,
      },
      labelStyle: {
        fontSize: '11px',
        fontFamily: 'Newsreader, serif',
        fill: '#404947',
      },
      labelBgStyle: {
        fill: '#ffffff',
        fillOpacity: 0.9,
      },
    }
  })

  // 2. Identify root node (node_type === 'root' or zero in-degree)
  let rootNode = rawNodes.find((n) => n.node_type === 'root')
  if (!rootNode) {
    rootNode = rawNodes.find((n) => (inDegreeMap.get(String(n.id)) || 0) === 0) || rawNodes[0]
  }
  const rootId = rootNode ? String(rootNode.id) : null

  // 3. Compute topological depth from root (BFS)
  const depthMap = new Map()
  if (rootId) {
    depthMap.set(rootId, 0)
    const queue = [rootId]
    while (queue.length > 0) {
      const current = queue.shift()
      const currentDepth = depthMap.get(current) || 0
      const children = childrenMap.get(current) || []
      for (const childId of children) {
        if (!depthMap.has(childId)) {
          depthMap.set(childId, currentDepth + 1)
          queue.push(childId)
        }
      }
    }
  }

  // Fallback depth for disconnected components
  for (const n of rawNodes) {
    const nid = String(n.id)
    if (!depthMap.has(nid)) {
      depthMap.set(nid, 1)
    }
  }

  // 4. Per-node coordinate validity (DR-08, P2-A fix)
  // A node has usable coordinates only if BOTH pos_x and pos_y are non-zero finite numbers.
  // We track per-node, not globally, so partially-positioned graphs don't stack unpositioned nodes.
  const nodeHasCoords = (n) =>
    Number.isFinite(Number(n.pos_x)) &&
    Number(n.pos_x) !== 0 &&
    Number.isFinite(Number(n.pos_y)) &&
    Number(n.pos_y) !== 0

  // Group nodes by depth for columnar layout
  const depthGroups = new Map()
  for (const n of rawNodes) {
    const nid = String(n.id)
    const depth = depthMap.get(nid) || 0
    if (!depthGroups.has(depth)) depthGroups.set(depth, [])
    depthGroups.get(depth).push(n)
  }

  const verticalSpacingByDepth = {
    0: 200,
    1: 180,
    2: 130,
    3: 110,
  }

  const flowNodes = rawNodes.map((n) => {
    const id = String(n.id)
    const isRoot = id === rootId || n.node_type === 'root'
    const isBranch = !isRoot && (n.node_type === 'branch' || depthMap.get(id) === 1)
    const nodeType = isRoot ? 'rootNode' : isBranch ? 'branchNode' : 'conceptNode'

    let x = Number(n.pos_x) || 0
    let y = Number(n.pos_y) || 0

    if (!nodeHasCoords(n)) {
      // This node has no valid stored coordinates — compute from layout
      const depth = depthMap.get(id) || 0
      const group = depthGroups.get(depth) || [n]
      const indexInGroup = group.findIndex((item) => String(item.id) === id)
      const spacing = verticalSpacingByDepth[Math.min(depth, 3)] || 120

      // X coordinate spaced by depth columns
      x = 80 + depth * 360

      // Y coordinate centered vertically around the group
      const totalGroupHeight = (group.length - 1) * spacing
      const startY = 320 - totalGroupHeight / 2
      y = startY + indexInGroup * spacing
    }

    const defaultTint = isRoot ? '#155E63' : isBranch ? '#C96B4B' : '#899B72'
    const tint = n.background_color || defaultTint

    return {
      id,
      type: nodeType,
      position: { x, y },
      data: {
        id,
        label: n.label || 'Untitled Concept',
        description: n.description || '',
        nodeType: n.node_type || (isRoot ? 'root' : isBranch ? 'branch' : 'leaf'),
        backgroundColor: tint,
        textColor: n.text_color || '#181D1A',
        category: n.node_data?.category || (isRoot ? 'Core Focus' : isBranch ? 'Branch' : 'Topic'),
        nodeData: n.node_data || {},
        incomingEdges: parentsMap.get(id) || [],
        outgoingEdges: childrenMap.get(id) || [],
        isEditing: options.editingNodeId ? String(options.editingNodeId) === id : false,
        onStartEditing: options.onStartEditing,
        onSaveLabel: options.onSaveLabel,
        onCancelEditing: options.onCancelEditing,
      },
    }
  })

  return { flowNodes, flowEdges }
}
