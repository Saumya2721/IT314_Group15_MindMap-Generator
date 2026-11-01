/**
 * Tests for graphAdapter.js — adapter between backend graph API and React Flow.
 *
 * Covers audit findings:
 *   P1-B: Edge labels must be read from 'label' field (not 'relationship_label' only)
 *   P2-A: Partially-positioned graphs must not stack unpositioned nodes at (0,0)
 *   Additional: Cross-links, all-positioned graphs, empty graphs
 */
import { describe, it, expect } from 'vitest'
import { adaptBackendGraphToFlow } from '../utils/graphAdapter'

// ─── Fixtures ──────────────────────────────────────────────────────────────────

const ROOT = { id: '1', node_type: 'root', label: 'Root', pos_x: 0, pos_y: 0 }
const BRANCH_A = { id: '2', node_type: 'default', label: 'Branch A', pos_x: 0, pos_y: 0 }
const BRANCH_B = { id: '3', node_type: 'default', label: 'Branch B', pos_x: 0, pos_y: 0 }
const LEAF = { id: '4', node_type: 'default', label: 'Leaf', pos_x: 0, pos_y: 0 }

const EDGE_WITH_LABEL = {
  id: 'e1',
  source_node_id: '1',
  target_node_id: '2',
  label: 'connects',
}
const EDGE_WITH_REL_LABEL = {
  id: 'e2',
  source_node_id: '2',
  target_node_id: '3',
  relationship_label: 'relates_to',
}
const EDGE_NO_LABEL = {
  id: 'e3',
  source_node_id: '3',
  target_node_id: '4',
}

// ─── Tests ─────────────────────────────────────────────────────────────────────

describe('adaptBackendGraphToFlow', () => {
  it('returns empty arrays for an empty node list', () => {
    const result = adaptBackendGraphToFlow([], [])
    expect(result.flowNodes).toEqual([])
    expect(result.flowEdges).toEqual([])
  })

  it('returns empty arrays when rawNodes is null/undefined', () => {
    const result = adaptBackendGraphToFlow(null, null)
    expect(result.flowNodes).toEqual([])
    expect(result.flowEdges).toEqual([])
  })

  describe('P1-B: Edge label field resolution', () => {
    it('reads edge label from the "label" field (primary backend field)', () => {
      const { flowEdges } = adaptBackendGraphToFlow([ROOT, BRANCH_A], [EDGE_WITH_LABEL])
      expect(flowEdges).toHaveLength(1)
      expect(flowEdges[0].label).toBe('connects')
    })

    it('falls back to "relationship_label" when "label" is absent', () => {
      const { flowEdges } = adaptBackendGraphToFlow(
        [ROOT, BRANCH_A, BRANCH_B],
        [EDGE_WITH_REL_LABEL],
      )
      expect(flowEdges[0].label).toBe('relates_to')
    })

    it('prefers "label" over "relationship_label" when both are present', () => {
      const edge = {
        id: 'e',
        source_node_id: '1',
        target_node_id: '2',
        label: 'primary',
        relationship_label: 'secondary',
      }
      const { flowEdges } = adaptBackendGraphToFlow([ROOT, BRANCH_A], [edge])
      expect(flowEdges[0].label).toBe('primary')
    })

    it('sets label to undefined when neither field is present', () => {
      const { flowEdges } = adaptBackendGraphToFlow(
        [ROOT, BRANCH_A, BRANCH_B, LEAF],
        [EDGE_NO_LABEL],
      )
      expect(flowEdges[0].label).toBeUndefined()
    })

    it('maps all edges 1:1 (no collapsing of cross-links)', () => {
      // LEAF has two parents — cross-link — should produce 2 edges
      const edges = [
        { id: 'e1', source_node_id: '2', target_node_id: '4', label: 'from-A' },
        { id: 'e2', source_node_id: '3', target_node_id: '4', label: 'from-B' },
      ]
      const { flowEdges } = adaptBackendGraphToFlow([ROOT, BRANCH_A, BRANCH_B, LEAF], edges)
      expect(flowEdges).toHaveLength(2)
    })
  })

  describe('P2-A: Per-node layout — partial coordinate handling', () => {
    it('assigns computed layout positions when all nodes have (0,0) coordinates', () => {
      const { flowNodes } = adaptBackendGraphToFlow(
        [ROOT, BRANCH_A, BRANCH_B],
        [
          { id: 'e1', source_node_id: '1', target_node_id: '2' },
          { id: 'e2', source_node_id: '1', target_node_id: '3' },
        ],
      )
      // Root at depth 0 → x = 80
      const root = flowNodes.find((n) => n.id === '1')
      expect(root.position.x).toBe(80)

      // Branch nodes at depth 1 → x = 80 + 1*360 = 440
      const branchA = flowNodes.find((n) => n.id === '2')
      const branchB = flowNodes.find((n) => n.id === '3')
      expect(branchA.position.x).toBe(440)
      expect(branchB.position.x).toBe(440)

      // Branch nodes should NOT all share the same y (no stacking)
      expect(branchA.position.y).not.toBe(branchB.position.y)
    })

    it('uses stored coordinates when a node has valid non-zero pos_x and pos_y', () => {
      const positionedNode = { id: '1', node_type: 'root', label: 'R', pos_x: 500, pos_y: 300 }
      const { flowNodes } = adaptBackendGraphToFlow([positionedNode], [])
      expect(flowNodes[0].position.x).toBe(500)
      expect(flowNodes[0].position.y).toBe(300)
    })

    it('computes layout for nodes with (0,0) even when other nodes have valid coords (partial)', () => {
      // positionedRoot has coords; unpositionedBranch does not
      const positionedRoot = { id: '1', node_type: 'root', label: 'R', pos_x: 100, pos_y: 200 }
      const unpositionedBranch = { id: '2', node_type: 'default', label: 'B', pos_x: 0, pos_y: 0 }
      const { flowNodes } = adaptBackendGraphToFlow(
        [positionedRoot, unpositionedBranch],
        [{ id: 'e1', source_node_id: '1', target_node_id: '2' }],
      )
      const root = flowNodes.find((n) => n.id === '1')
      const branch = flowNodes.find((n) => n.id === '2')

      // Root keeps its stored position
      expect(root.position.x).toBe(100)
      expect(root.position.y).toBe(200)

      // Branch gets computed position (not 0,0 stacked)
      expect(branch.position.x).not.toBe(0)
      expect(branch.position.y).not.toBe(0)
    })
  })

  describe('Node type mapping', () => {
    it('maps node_type root to rootNode React Flow type', () => {
      const { flowNodes } = adaptBackendGraphToFlow([ROOT], [])
      expect(flowNodes[0].type).toBe('rootNode')
    })

    it('maps depth-1 nodes (default type) to branchNode', () => {
      const { flowNodes } = adaptBackendGraphToFlow(
        [ROOT, BRANCH_A],
        [{ id: 'e1', source_node_id: '1', target_node_id: '2' }],
      )
      const branch = flowNodes.find((n) => n.id === '2')
      expect(branch.type).toBe('branchNode')
    })

    it('maps depth-2+ nodes to conceptNode', () => {
      const { flowNodes } = adaptBackendGraphToFlow(
        [ROOT, BRANCH_A, LEAF],
        [
          { id: 'e1', source_node_id: '1', target_node_id: '2' },
          { id: 'e2', source_node_id: '2', target_node_id: '4' },
        ],
      )
      const leaf = flowNodes.find((n) => n.id === '4')
      expect(leaf.type).toBe('conceptNode')
    })
  })
})
