import { describe, it, expect, vi } from 'vitest'
import React from 'react'
import OutlineView, { buildTree } from '../components/canvas/OutlineView'
import { renderMarkdown, renderInlineMarkdown } from '../components/canvas/ChatPanel'

describe('OutlineView: Tree Construction & Edge Hierarchy', () => {
  const sampleNodes = [
    {
      id: 'root-1',
      label: 'Artificial Intelligence',
      node_type: 'root',
      description: 'The science of making intelligent machines.',
      category: 'Root',
    },
    {
      id: 'branch-1',
      label: 'Machine Learning',
      node_type: 'branch',
      description: 'Algorithms that learn from empirical data.',
      category: 'Branch',
    },
    {
      id: 'concept-1',
      label: 'Loss Functions',
      node_type: 'concept',
      description: 'L(y, \\hat{y}) = -\\sum y_i \\log(\\hat{y}_i)',
      category: 'Topic',
    },
    {
      id: 'floating-1',
      label: 'Ethics & Safety',
      node_type: 'concept',
      description: 'Alignment research.',
      category: 'Topic',
    },
  ]

  const sampleEdges = [
    {
      id: 'e1',
      source_node_id: 'root-1',
      target_node_id: 'branch-1',
      label: 'sub-field',
    },
    {
      id: 'e2',
      source_node_id: 'branch-1',
      target_node_id: 'concept-1',
      label: 'formulated as',
    },
  ]

  it('buildTree reconstructs deep hierarchy from edges with labels', () => {
    const { rootTree, floatingNodes } = buildTree(sampleNodes, sampleEdges)

    expect(rootTree).toBeDefined()
    expect(rootTree.node.id).toBe('root-1')
    expect(rootTree.children.length).toBe(1)

    const branch = rootTree.children[0]
    expect(branch.node.id).toBe('branch-1')
    expect(branch.incomingEdgeLabel).toBe('sub-field')
    expect(branch.children.length).toBe(1)

    const leaf = branch.children[0]
    expect(leaf.node.id).toBe('concept-1')
    expect(leaf.incomingEdgeLabel).toBe('formulated as')

    // Floating unconnected node captured separately
    expect(floatingNodes.length).toBe(1)
    expect(floatingNodes[0].id).toBe('floating-1')
  })

  it('handles empty nodes gracefully without throwing', () => {
    const { rootTree, floatingNodes } = buildTree([], [])
    expect(rootTree).toBeNull()
    expect(floatingNodes).toEqual([])
  })

  it('exports OutlineView as default export component', () => {
    expect(typeof OutlineView).toBe('function')
  })
})

describe('ChatPanel: Interactive Node Reference Parsing', () => {
  const nodes = [
    {
      id: '8a302a60-f5fc-4f22-92c0-2b797b00373c',
      label: 'Policy Gradient',
      node_type: 'branch',
      background_color: '#C96B4B',
    },
    {
      id: 'e036e84b-8032-42e8-84d6-6ea6adf4f115',
      label: 'Naive Bayes',
      node_type: 'concept',
      background_color: '#899B72',
    },
  ]

  const nodeMap = new Map(nodes.map((n) => [String(n.id), n]))

  it('transforms [node:<uuid>] tags into interactive node pill buttons', () => {
    const onSelectNode = vi.fn()
    const content =
      'I suggest reviewing **Policy Gradient** [node:8a302a60-f5fc-4f22-92c0-2b797b00373c] for detail.'
    const result = renderInlineMarkdown(content, { nodeMap, onSelectNode })

    expect(Array.isArray(result)).toBe(true)
    const pill = result.find(
      (part) => React.isValidElement(part) && part.props.className === 'chat-node-pill',
    )
    expect(pill).toBeDefined()
    expect(pill.props.title).toContain('Policy Gradient')

    pill.props.onClick()
    expect(onSelectNode).toHaveBeenCalledWith(
      expect.objectContaining({
        id: '8a302a60-f5fc-4f22-92c0-2b797b00373c',
        label: 'Policy Gradient',
      }),
    )
  })

  it('converts bare UUIDs matching known nodes into interactive node pills', () => {
    const onSelectNode = vi.fn()
    const content = 'Please inspect 8a302a60-f5fc-4f22-92c0-2b797b00373c on the map.'
    const result = renderInlineMarkdown(content, { nodeMap, onSelectNode })

    const pill = result.find(
      (part) => React.isValidElement(part) && part.props.className === 'chat-node-pill',
    )
    expect(pill).toBeDefined()
    expect(pill.props.title).toContain('Policy Gradient')

    pill.props.onClick()
    expect(onSelectNode).toHaveBeenCalledWith(
      expect.objectContaining({
        id: '8a302a60-f5fc-4f22-92c0-2b797b00373c',
        label: 'Policy Gradient',
      }),
    )
  })

  it('renders bold, code, and italic markdown elements alongside node pills', () => {
    const content =
      '**Bold Title** `code_snippet` *italic note* [node:8a302a60-f5fc-4f22-92c0-2b797b00373c]'
    const result = renderInlineMarkdown(content, { nodeMap })

    const boldEl = result.find((p) => React.isValidElement(p) && p.type === 'strong')
    expect(boldEl).toBeDefined()

    const codeEl = result.find((p) => React.isValidElement(p) && p.type === 'code')
    expect(codeEl).toBeDefined()

    const italicEl = result.find((p) => React.isValidElement(p) && p.type === 'em')
    expect(italicEl).toBeDefined()

    const pill = result.find(
      (p) => React.isValidElement(p) && p.props.className === 'chat-node-pill',
    )
    expect(pill).toBeDefined()
  })

  it('renders multi-line markdown with lists and headings', () => {
    const md = `### Summary Heading
* Point one
* Point two mentioning [node:8a302a60-f5fc-4f22-92c0-2b797b00373c]

Final paragraph.`
    const elements = renderMarkdown(md, { nodeMap })
    expect(Array.isArray(elements)).toBe(true)
    expect(elements.length).toBeGreaterThan(0)
  })
})
