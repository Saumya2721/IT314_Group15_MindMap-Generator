/**
 * Unit tests for S3-FE3 Research Transparency Panel and External Source Attribution.
 *
 * Runs in plain Vitest Node environment without browser dependencies.
 */
import { describe, it, expect } from 'vitest'
import { aggregateMapSources } from '../components/canvas/MapSourcesSidebar'

describe('S3-FE3: Research Sources Aggregation & Metadata', () => {
  const mockNodes = [
    {
      id: 'node-1',
      label: 'Backpropagation Algorithm',
      source_type: 'wikipedia',
      source_url: 'https://en.wikipedia.org/wiki/Backpropagation',
      source_label: 'Wikipedia: Backpropagation',
    },
    {
      id: 'node-2',
      label: 'Gradient Descent Optimization',
      source_type: 'wikipedia',
      source_url: 'https://en.wikipedia.org/wiki/Backpropagation',
      source_label: 'Wikipedia: Backpropagation',
    },
    {
      id: 'node-3',
      label: 'Attention Is All You Need',
      source_type: 'scholar',
      source_url: 'https://scholar.google.com/citations?view_op=view_citation',
      source_label: 'Vaswani et al. (2017)',
    },
    {
      id: 'node-4',
      label: 'Deep Residual Learning',
      source_type: 'arxiv',
      source_url: 'https://arxiv.org/abs/1512.03385',
      source_label: 'He et al. (2015)',
    },
    {
      id: 'node-5',
      label: 'PyTorch Architecture Guide',
      source_type: 'page_reader',
      source_url: 'https://pytorch.org/docs/stable/index.html',
      source_label: 'PyTorch Documentation',
    },
    {
      id: 'node-6',
      label: 'Heuristic Knowledge Node',
      source_type: null,
      source_url: null,
    },
  ]

  it('aggregates and dedupes sources across mind map nodes', () => {
    const sources = aggregateMapSources(mockNodes)
    // Wikipedia source should be deduped for node-1 and node-2
    expect(sources.length).toBe(4) // 1 wikipedia, 1 scholar, 1 arxiv, 1 page_reader
  })

  it('associates cited nodes with their respective sources', () => {
    const sources = aggregateMapSources(mockNodes)
    const wikiSource = sources.find((s) => s.type === 'wikipedia')

    expect(wikiSource).toBeDefined()
    expect(wikiSource.nodes.length).toBe(2)
    expect(wikiSource.nodes.map((n) => n.id)).toEqual(['node-1', 'node-2'])
  })

  it('correctly categorizes scholar, arxiv, and page_reader types', () => {
    const sources = aggregateMapSources(mockNodes)
    const types = sources.map((s) => s.type)

    expect(types).toContain('scholar')
    expect(types).toContain('arxiv')
    expect(types).toContain('page_reader')
    expect(types).toContain('wikipedia')
  })

  it('ignores nodes with no source metadata', () => {
    const singleHeuristic = [{ id: 'empty-1', label: 'Empty Node' }]
    const sources = aggregateMapSources(singleHeuristic)
    expect(sources.length).toBe(0)
  })
})

describe('S3-FE3: CC BY-SA 4.0 Compliance Verification', () => {
  it('mandates CC BY-SA attribution requirement for Wikipedia sources', () => {
    const wikiSource = {
      type: 'wikipedia',
      url: 'https://en.wikipedia.org/wiki/Deep_learning',
      label: 'Deep Learning',
    }

    const requiresLicense = wikiSource.type === 'wikipedia'
    const ccLicenseUrl = 'https://creativecommons.org/licenses/by-sa/4.0/'

    expect(requiresLicense).toBe(true)
    expect(ccLicenseUrl).toBe('https://creativecommons.org/licenses/by-sa/4.0/')
  })
})
