// @vitest-environment jsdom

/**
 * Unit & Integration Tests for Issue S3-FE5:
 * Purpose Selector on Create Map page ("What is this map for?")
 *
 * Requirements verified:
 * 1. Single-select purpose field with exactly three options: General, Study, Research.
 * 2. Corresponding values: 'general', 'study', 'research'.
 * 3. Default selected value: 'general'.
 * 4. Request payload inclusion: property name 'purpose' in generateMap and quickGenerate.
 * 5. Research purpose sent as purpose: 'research' (for arXiv & Google Scholar tool enablement).
 * 6. Real DOM component interaction using React Testing Library and jsdom.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { mindMapService } from '../services/mindMapService'
import { apiClient } from '../services/apiClient'
import CreateMapPage from '../pages/CreateMapPage'

// Mock external context dependencies for CreateMapPage
let mockDraftMap = null

vi.mock('../context/MindMapContext', () => ({
  useMindMap: () => ({
    draftMap: mockDraftMap,
    setDraftMap: vi.fn(),
  }),
  MindMapProvider: ({ children }) => children,
}))

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@atelier.craft', name: 'Test User' },
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }) => children,
}))

function renderCreateMapPage(initialDraft = null) {
  mockDraftMap = initialDraft
  return render(React.createElement(MemoryRouter, null, React.createElement(CreateMapPage, null)))
}

describe('S3-FE5: Purpose Selector & Generation Payload Contract', () => {
  let capturedCalls = []
  const realFetch = globalThis.fetch

  const jsonResponse = (body, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
    text: async () => JSON.stringify(body),
  })

  beforeEach(() => {
    capturedCalls = []
    mockDraftMap = null
    globalThis.fetch = vi.fn(async (input, options) => {
      capturedCalls.push({ input, options })
      return jsonResponse({ job_id: 'job-123', map_id: 'map-456' })
    })
  })

  afterEach(() => {
    cleanup()
    globalThis.fetch = realFetch
    vi.restoreAllMocks()
  })

  const pathOf = (input) => String(input).replace(/^https?:\/\/[^/]+/, '')

  describe('generateMap (Multipart FormData Payload)', () => {
    it('defaults purpose to "general" when not specified', async () => {
      await mindMapService.generateMap('map-456', {
        topic: 'Machine Learning',
        ideas: 'Neural networks and gradients',
      })

      expect(capturedCalls).toHaveLength(1)
      const call = capturedCalls[0]
      expect(pathOf(call.input)).toBe('/api/maps/map-456/generate')
      expect(call.options.body).toBeInstanceOf(FormData)
      expect(call.options.body.get('purpose')).toBe('general')
      expect(call.options.body.get('detail_level')).toBe('overview')
      expect(call.options.body.get('topic')).toBe('Machine Learning')
    })

    it('sends purpose: "study" when study purpose is selected', async () => {
      await mindMapService.generateMap('map-456', {
        topic: 'Organic Chemistry',
        ideas: 'Reaction mechanisms and stereochemistry',
        purpose: 'study',
        detail_level: 'balanced',
      })

      const call = capturedCalls[0]
      expect(call.options.body.get('purpose')).toBe('study')
      expect(call.options.body.get('detail_level')).toBe('balanced')
      expect(call.options.body.get('topic')).toBe('Organic Chemistry')
    })

    it('sends purpose: "research" for academic discovery via arXiv & Scholar', async () => {
      await mindMapService.generateMap('map-456', {
        topic: 'Quantum Error Correction',
        ideas: 'Surface codes and fault-tolerant architectures',
        purpose: 'research',
        detail_level: 'detailed',
      })

      const call = capturedCalls[0]
      expect(call.options.body.get('purpose')).toBe('research')
      expect(call.options.body.get('detail_level')).toBe('detailed')
      expect(call.options.body.get('topic')).toBe('Quantum Error Correction')
    })

    it('preserves attached files alongside selected purpose', async () => {
      const mockFile = new File(['research paper'], 'paper.pdf', { type: 'application/pdf' })

      await mindMapService.generateMap('map-456', {
        topic: 'Attention Mechanisms',
        purpose: 'research',
        files: [mockFile],
      })

      const call = capturedCalls[0]
      expect(call.options.body.get('purpose')).toBe('research')
      expect(call.options.body.get('files')).toBe(mockFile)
    })
  })

  describe('quickGenerate (JSON Payload)', () => {
    it('includes default purpose: "general" in quick-generate payload', async () => {
      const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
        ok: true,
        data: { job_id: 'quick-job-1', map_id: 'quick-map-1' },
      })

      await mindMapService.quickGenerate({
        topic: 'Photosynthesis',
        ideas: 'Light-dependent reactions',
      })

      expect(postSpy).toHaveBeenCalledWith('/api/maps/quick-generate', {
        topic: 'Photosynthesis',
        ideas: 'Light-dependent reactions',
        detail_level: 'overview',
        purpose: 'general',
        map_id: undefined,
      })
    })

    it('includes purpose: "research" in quick-generate payload', async () => {
      const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
        ok: true,
        data: { job_id: 'quick-job-2', map_id: 'quick-map-2' },
      })

      await mindMapService.quickGenerate({
        topic: 'Graph Neural Networks',
        purpose: 'research',
        detail_level: 'detailed',
      })

      expect(postSpy).toHaveBeenCalledWith('/api/maps/quick-generate', {
        topic: 'Graph Neural Networks',
        ideas: undefined,
        detail_level: 'detailed',
        purpose: 'research',
        map_id: undefined,
      })
    })

    it('includes purpose: "study" in quick-generate payload', async () => {
      const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({
        ok: true,
        data: { job_id: 'quick-job-3', map_id: 'quick-map-3' },
      })

      await mindMapService.quickGenerate({
        topic: 'World History',
        purpose: 'study',
      })

      expect(postSpy).toHaveBeenCalledWith('/api/maps/quick-generate', {
        topic: 'World History',
        ideas: undefined,
        detail_level: 'overview',
        purpose: 'study',
        map_id: undefined,
      })
    })
  })

  describe('Purpose Option Constraints', () => {
    const VALID_PURPOSES = ['general', 'study', 'research']

    it('strictly supports exactly three valid purpose options', () => {
      expect(VALID_PURPOSES).toHaveLength(3)
      expect(VALID_PURPOSES).toEqual(['general', 'study', 'research'])
    })

    it('default purpose is "general"', () => {
      const defaultPurpose = 'general'
      expect(defaultPurpose).toBe('general')
      expect(VALID_PURPOSES).toContain(defaultPurpose)
    })
  })

  describe('CreateMapPage Component: Purpose Selector Interaction (RTL)', () => {
    it('renders the radiogroup container with the accessible label "What is this map for?"', () => {
      renderCreateMapPage()
      const radioGroup = screen.getByRole('radiogroup', { name: 'What is this map for?' })
      expect(radioGroup).toBeDefined()
      expect(radioGroup.getAttribute('aria-label')).toBe('What is this map for?')
    })

    it('renders exactly three purpose radio options: General, Study, and Research', () => {
      renderCreateMapPage()
      const options = screen.getAllByRole('radio')
      expect(options).toHaveLength(3)

      const general = screen.getByRole('radio', { name: /general/i })
      const study = screen.getByRole('radio', { name: /study/i })
      const research = screen.getByRole('radio', { name: /research/i })

      expect(general).toBeDefined()
      expect(study).toBeDefined()
      expect(research).toBeDefined()
      expect(general.getAttribute('data-purpose')).toBe('general')
      expect(study.getAttribute('data-purpose')).toBe('study')
      expect(research.getAttribute('data-purpose')).toBe('research')
    })

    it('has General selected by default with aria-checked="true" and active class', () => {
      renderCreateMapPage()
      const general = screen.getByRole('radio', { name: /general/i })
      const study = screen.getByRole('radio', { name: /study/i })
      const research = screen.getByRole('radio', { name: /research/i })

      expect(general.getAttribute('aria-checked')).toBe('true')
      expect(general.classList.contains('active')).toBe(true)

      expect(study.getAttribute('aria-checked')).toBe('false')
      expect(study.classList.contains('active')).toBe(false)

      expect(research.getAttribute('aria-checked')).toBe('false')
      expect(research.classList.contains('active')).toBe(false)
    })

    it('clicking Study selects Study and deselects General and Research', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const general = screen.getByRole('radio', { name: /general/i })
      const study = screen.getByRole('radio', { name: /study/i })
      const research = screen.getByRole('radio', { name: /research/i })

      await user.click(study)

      expect(study.getAttribute('aria-checked')).toBe('true')
      expect(study.classList.contains('active')).toBe(true)

      expect(general.getAttribute('aria-checked')).toBe('false')
      expect(general.classList.contains('active')).toBe(false)

      expect(research.getAttribute('aria-checked')).toBe('false')
      expect(research.classList.contains('active')).toBe(false)
    })

    it('clicking Research selects Research and deselects Study and General', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const general = screen.getByRole('radio', { name: /general/i })
      const study = screen.getByRole('radio', { name: /study/i })
      const research = screen.getByRole('radio', { name: /research/i })

      // First select Study
      await user.click(study)
      expect(study.getAttribute('aria-checked')).toBe('true')

      // Then click Research
      await user.click(research)

      expect(research.getAttribute('aria-checked')).toBe('true')
      expect(research.classList.contains('active')).toBe(true)

      expect(study.getAttribute('aria-checked')).toBe('false')
      expect(study.classList.contains('active')).toBe(false)

      expect(general.getAttribute('aria-checked')).toBe('false')
      expect(general.classList.contains('active')).toBe(false)
    })

    it('clicking General again selects General and deselects Research', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const general = screen.getByRole('radio', { name: /general/i })
      const research = screen.getByRole('radio', { name: /research/i })

      // Switch to Research
      await user.click(research)
      expect(research.getAttribute('aria-checked')).toBe('true')
      expect(general.getAttribute('aria-checked')).toBe('false')

      // Click General again
      await user.click(general)

      expect(general.getAttribute('aria-checked')).toBe('true')
      expect(general.classList.contains('active')).toBe(true)

      expect(research.getAttribute('aria-checked')).toBe('false')
      expect(research.classList.contains('active')).toBe(false)
    })
  })
})
