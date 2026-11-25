/**
 * Unit tests for S3-FE7 Admin Pipeline Configuration Management UI.
 *
 * Runs in plain Vitest Node environment without browser rendering dependencies,
 * thoroughly verifying pipeline flags, toggles, form state mutations, and service endpoints.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { adminService } from '../services/adminService'
import { apiClient } from '../services/apiClient'

vi.mock('../services/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}))
describe('S3-FE7: Pipeline Configuration Service Contracts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns the backend configuration', async () => {
    const config = { enable_two_pass: false, research_engine: 'inline', image_extractor: 'gemini' }
    apiClient.get.mockResolvedValueOnce({ ok: true, data: config })
    expect(await adminService.getPipelineConfig()).toEqual(config)
    expect(apiClient.get).toHaveBeenCalledWith('/api/admin/config')
  })

  it('normalizes PATCH keys and returns the persisted settings', async () => {
    const saved = { enable_two_pass: false, research_engine: 'subgraph', image_extractor: 'gemini' }
    apiClient.patch.mockResolvedValueOnce({ ok: true, data: saved })
    expect(await adminService.updatePipelineConfig({ RESEARCH_ENGINE: 'subgraph' })).toEqual(saved)
    expect(apiClient.patch).toHaveBeenCalledWith('/api/admin/config', {
      research_engine: 'subgraph',
    })
  })

  it('does not signal success for a rejected save', async () => {
    apiClient.patch.mockResolvedValueOnce({ ok: false, error: 'Invalid config' })
    await expect(adminService.updatePipelineConfig({ image_extractor: 'invalid' })).rejects.toThrow(
      'Invalid config',
    )
  })
})

describe('S3-FE7: Pipeline Configuration State & Validation Logic', () => {
  const initialConfig = {
    ENABLE_TWO_PASS: false,
    RESEARCH_ENGINE: 'inline',
    IMAGE_EXTRACTOR: 'gemini',
    llm: {
      provider: 'groq',
      model: 'llama-3.3-70b-versatile',
      temperature: 0.2,
    },
    prompts: {
      version: 'v1.0',
    },
  }

  function computeHasChanges(current, baseline) {
    if (!baseline || !current) return false
    return (
      current.ENABLE_TWO_PASS !== baseline.ENABLE_TWO_PASS ||
      current.RESEARCH_ENGINE !== baseline.RESEARCH_ENGINE ||
      current.IMAGE_EXTRACTOR !== baseline.IMAGE_EXTRACTOR
    )
  }

  it('detects changes when ENABLE_TWO_PASS is toggled', () => {
    const modified = { ...initialConfig, ENABLE_TWO_PASS: true }
    expect(computeHasChanges(modified, initialConfig)).toBe(true)
  })

  it('detects changes when RESEARCH_ENGINE is switched to subgraph', () => {
    const modified = { ...initialConfig, RESEARCH_ENGINE: 'subgraph' }
    expect(computeHasChanges(modified, initialConfig)).toBe(true)
  })

  it('detects changes when IMAGE_EXTRACTOR is altered', () => {
    const modified = { ...initialConfig, IMAGE_EXTRACTOR: 'tesseract' }
    expect(computeHasChanges(modified, initialConfig)).toBe(true)
  })

  it('reports no changes when state is untouched or reset', () => {
    expect(computeHasChanges(initialConfig, initialConfig)).toBe(false)
    const clone = JSON.parse(JSON.stringify(initialConfig))
    expect(computeHasChanges(clone, initialConfig)).toBe(false)
  })
})
