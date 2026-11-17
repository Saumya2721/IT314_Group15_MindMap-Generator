import React, { useState, useMemo, useCallback } from 'react'
import './OutlineView.css'

/**
 * Build an acyclic recursive tree representation from backend/canvas nodes and edges.
 */
export function buildTree(rawNodes = [], rawEdges = []) {
  if (!rawNodes || rawNodes.length === 0) {
    return { rootTree: null, floatingNodes: [] }
  }

  const nodesById = new Map()
  for (const n of rawNodes) {
    nodesById.set(String(n.id), n)
  }

  // Build parent lookup and children adjacency with edge labels
  const parentLookup = new Map() // childId -> { parentId, edgeLabel }
  const childrenMap = new Map() // parentId -> [{ childNode, edgeLabel }]

  for (const edge of rawEdges || []) {
    const srcId = String(edge.source_node_id || edge.source)
    const tgtId = String(edge.target_node_id || edge.target)
    const label = (
      edge.label ||
      edge.relationship_label ||
      edge.data?.label ||
      edge.data?.relationship_label ||
      ''
    ).trim()

    if (srcId && tgtId && nodesById.has(srcId) && nodesById.has(tgtId)) {
      parentLookup.set(tgtId, { parentId: srcId, edgeLabel: label })
      if (!childrenMap.has(srcId)) {
        childrenMap.set(srcId, [])
      }
      childrenMap.get(srcId).push({
        childNode: nodesById.get(tgtId),
        edgeLabel: label,
      })
    }
  }

  // Find root node (node_type === 'root' or zero in-degree)
  let rootNode = rawNodes.find((n) => n.node_type === 'root')
  if (!rootNode) {
    rootNode = rawNodes.find((n) => !parentLookup.has(String(n.id))) || rawNodes[0]
  }

  const visited = new Set()

  function constructNodeItem(node, incomingEdgeLabel = '', branchAncestors = new Set()) {
    const nid = String(node.id)
    visited.add(nid)
    const nextAncestors = new Set(branchAncestors)
    nextAncestors.add(nid)

    const rawChildren = childrenMap.get(nid) || []
    const children = []
    for (const c of rawChildren) {
      const cid = String(c.childNode.id)
      if (!nextAncestors.has(cid)) {
        children.push(constructNodeItem(c.childNode, c.edgeLabel, nextAncestors))
      }
    }

    return {
      node,
      incomingEdgeLabel,
      children,
    }
  }

  const rootTree = rootNode ? constructNodeItem(rootNode, '') : null

  // Capture any disconnected subtrees / secondary roots that have children
  const secondaryRoots = []
  for (const n of rawNodes) {
    const nid = String(n.id)
    if (!visited.has(nid) && (childrenMap.get(nid) || []).length > 0) {
      secondaryRoots.push(constructNodeItem(n, ''))
    }
  }

  // Collect any floating/unconnected nodes not visited from root or secondary trees
  const floatingNodes = rawNodes.filter((n) => !visited.has(String(n.id)))

  return { rootTree, secondaryRoots, floatingNodes }
}

/**
 * Individual recursive outline node item.
 */
function OutlineNodeItem({
  item,
  depth = 0,
  collapsedSet,
  onToggleCollapse,
  onSelectNode,
}) {
  const { node, incomingEdgeLabel, children } = item
  const nid = String(node.id)
  const hasChildren = children && children.length > 0
  const isCollapsed = collapsedSet.has(nid)

  // Bullet style by depth
  const renderBullet = () => {
    if (depth === 0) return '●'
    if (depth === 1) return '●'
    if (depth === 2) return '○'
    if (depth === 3) return '▪'
    return '–'
  }

  const typeClass =
    node.node_type === 'root'
      ? 'root'
      : node.node_type === 'branch'
        ? 'branch'
        : 'concept'

  const isFormulaOrCode =
    node.description &&
    (node.description.includes('\\') ||
      node.description.includes('=') ||
      node.description.includes('`') ||
      node.description.length > 80)

  return (
    <li className={`outline-item outline-item-depth-${Math.min(depth, 5)}`}>
      <div className="outline-item-row">
        {/* Expand / Collapse toggle button */}
        {hasChildren ? (
          <button
            type="button"
            className="outline-collapse-btn"
            onClick={() => onToggleCollapse(nid)}
            title={isCollapsed ? 'Expand sub-topics' : 'Collapse sub-topics'}
            aria-label={isCollapsed ? 'Expand' : 'Collapse'}
          >
            <span
              className="material-symbols-outlined"
              style={{ fontSize: '15px' }}
            >
              {isCollapsed ? 'chevron_right' : 'expand_more'}
            </span>
          </button>
        ) : (
          <span style={{ width: 18, height: 18, flexShrink: 0 }} />
        )}

        {/* Visual bullet marker */}
        <span className="outline-bullet">{renderBullet()}</span>

        {/* Node Content */}
        <div className="outline-node-content">
          <div className="outline-node-header">
            <span
              className="outline-node-label"
              onClick={() => onSelectNode?.(node)}
              style={{ cursor: onSelectNode ? 'pointer' : 'default' }}
              title={onSelectNode ? 'Click to highlight node' : undefined}
            >
              {node.label || node.title || 'Untitled Concept'}
            </span>

            {/* Edge relationship badge */}
            {incomingEdgeLabel && (
              <span className="outline-edge-badge">
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: '12px' }}
                >
                  arrow_forward
                </span>
                <span>{incomingEdgeLabel}</span>
              </span>
            )}

            {/* Node hierarchy badge */}
            <span className={`outline-type-badge ${typeClass}`}>
              {node.category || node.node_type || 'concept'}
            </span>
          </div>

          {/* Description / Mathematical Formula */}
          {node.description && (
            isFormulaOrCode ? (
              <div className="outline-formula-block">
                {node.description}
              </div>
            ) : (
              <p className="outline-node-desc">{node.description}</p>
            )
          )}
        </div>
      </div>

      {/* Recursive Children Sub-list */}
      {hasChildren && !isCollapsed && (
        <ul className="outline-children-list">
          {children.map((childItem) => (
            <OutlineNodeItem
              key={childItem.node.id}
              item={childItem}
              depth={depth + 1}
              collapsedSet={collapsedSet}
              onToggleCollapse={onToggleCollapse}
              onSelectNode={onSelectNode}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

/**
 * Main OutlineView Component
 */
export default function OutlineView({
  nodes = [],
  edges = [],
  mapTitle = 'Untitled Mind Map',
  onSwitchToCanvas,
  onSelectNode,
}) {
  const [collapsedSet, setCollapsedSet] = useState(new Set())

  const { rootTree, secondaryRoots = [], floatingNodes = [] } = useMemo(() => {
    return buildTree(nodes, edges)
  }, [nodes, edges])

  const toggleCollapse = useCallback((id) => {
    setCollapsedSet((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const handleExpandAll = useCallback(() => {
    setCollapsedSet(new Set())
  }, [])

  const handleCollapseAll = useCallback(() => {
    // Collapse all branches (depth >= 1)
    const toCollapse = new Set()
    for (const n of nodes) {
      if (n.node_type !== 'root') {
        toCollapse.add(String(n.id))
      }
    }
    setCollapsedSet(toCollapse)
  }, [nodes])

  if (!nodes || nodes.length === 0) {
    return (
      <div className="outline-view-container">
        <div className="outline-card">
          <h2 className="outline-title">{mapTitle}</h2>
          <p style={{ color: 'var(--ink-secondary)' }}>
            No concepts available on this mind map yet.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: '16px' }}
            onClick={onSwitchToCanvas}
          >
            Switch to Visual Canvas
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="outline-view-container">
      <div className="outline-card">
        {/* Header with Title & Quick Expand/Collapse */}
        <div className="outline-header">
          <div className="outline-header-left">
            <h1 className="outline-title">{mapTitle}</h1>
            <p className="outline-subtitle">
              Hierarchical mind map outline &bull; {nodes.length} concepts &bull; {edges.length} connections
            </p>
          </div>

          <div className="outline-controls">
            <button
              type="button"
              className="outline-ctrl-btn"
              onClick={handleExpandAll}
              title="Expand all branches"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '15px' }}>
                unfold_more
              </span>
              <span>Expand All</span>
            </button>
            <button
              type="button"
              className="outline-ctrl-btn"
              onClick={handleCollapseAll}
              title="Collapse sub-topics"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '15px' }}>
                unfold_less
              </span>
              <span>Collapse All</span>
            </button>
          </div>
        </div>

        {/* Tree List */}
        <ul className="outline-tree-root">
          {rootTree && (
            <OutlineNodeItem
              item={rootTree}
              depth={0}
              collapsedSet={collapsedSet}
              onToggleCollapse={toggleCollapse}
              onSelectNode={onSelectNode}
            />
          )}

          {secondaryRoots.map((sr) => (
            <OutlineNodeItem
              key={sr.node.id}
              item={sr}
              depth={0}
              collapsedSet={collapsedSet}
              onToggleCollapse={toggleCollapse}
              onSelectNode={onSelectNode}
            />
          ))}

          {/* Floating / Unconnected Concepts */}
          {floatingNodes.length > 0 && (
            <li className="outline-item" style={{ marginTop: '24px' }}>
              <div
                style={{
                  padding: '8px 12px',
                  backgroundColor: '#f6f8f7',
                  borderRadius: '6px',
                  border: '1px solid #dce4e2',
                  marginBottom: '10px',
                }}
              >
                <strong style={{ fontSize: '14px', color: '#155e63' }}>
                  Additional Notes &amp; Free Concepts ({floatingNodes.length})
                </strong>
              </div>
              <ul className="outline-children-list" style={{ borderLeft: 'none' }}>
                {floatingNodes.map((fn) => (
                  <OutlineNodeItem
                    key={fn.id}
                    item={{ node: fn, incomingEdgeLabel: '', children: [] }}
                    depth={1}
                    collapsedSet={collapsedSet}
                    onToggleCollapse={toggleCollapse}
                    onSelectNode={onSelectNode}
                  />
                ))}
              </ul>
            </li>
          )}
        </ul>

        {/* Footer */}
        <div className="outline-footer">
          <span style={{ fontSize: '12px', color: 'var(--ink-secondary)' }}>
            Switch back to visual canvas to freely position, drag, or connect concepts.
          </span>
          <button
            type="button"
            className="btn btn-primary"
            onClick={onSwitchToCanvas}
          >
            Switch to Visual Canvas
          </button>
        </div>
      </div>
    </div>
  )
}
