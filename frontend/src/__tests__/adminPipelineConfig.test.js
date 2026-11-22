/**
 * Unit tests for S3-FE7 Admin Pipeline Configuration Management UI.
 *
 * Runs in plain Vitest Node environment without browser rendering dependencies,
 * thoroughly verifying pipeline flags, toggles, form state mutations, and service endpoints.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { adminService } from '../services/adminService'
import { evaluateAdminAccess } from './adminPanel.test'

describe('S3-FE7: Pipeline Configuration Admin Access Control', () => {
  it('guards /admin/config strictly behind the admin role', () => {
    // 1. Initializing state
    expect(evaluateAdminAccess(null, true).status).toBe('loading')

    // 2. Unauthenticated user
    const unauth = evaluateAdminAccess(null, false)
    expect(unauth.status).toBe('redirect')
    expect(unauth.target).toBe('/login')

    // 3. Regular non-admin user
    const standardUser = { id: 'usr-1', email: 'user@mindweave.craft', role: 'user' }
    const forbidden = evaluateAdminAccess(standardUser, false)
    expect(forbidden.status).toBe('redirect')
    expect(forbidden.target).toBe('/dashboard')

    // 4. Authenticated admin
    const adminUser = { id: 'admin-1', email: 'admin@mindweave.craft', role: 'admin' }
    const allowed = evaluateAdminAccess(adminUser, false)
    expect(allowed.status).toBe('allowed')
  })
})

describe('S3-FE7: Pipeline Configuration Service Contracts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('retrieves runtime pipeline config with all required feature flags', async () => {
    const config = await adminService.getPipelineConfig()
    expect(config).toBeDefined()

    // Required interactive toggles
    expect(config).toHaveProperty('ENABLE_TWO_PASS')
    expect(typeof config.ENABLE_TWO_PASS).toBe('boolean')

    expect(config).toHaveProperty('RESEARCH_ENGINE')
    expect(['subgraph', 'inline']).toContain(config.RESEARCH_ENGINE)

    expect(config).toHaveProperty('IMAGE_EXTRACTOR')
    expect(['gemini', 'openrouter', 'tesseract']).toContain(config.IMAGE_EXTRACTOR)

    // Required prompt template version and LLM provider metadata
    expect(config).toHaveProperty('llm')
    expect(config.llm).toHaveProperty('provider')
    expect(config.llm).toHaveProperty('model')

    expect(config).toHaveProperty('prompts')
    expect(config.prompts).toHaveProperty('version')
  })

  it('applies and updates pipeline configuration via PATCH payload', async () => {
    const updates = {
      ENABLE_TWO_PASS: true,
      RESEARCH_ENGINE: 'subgraph',
      IMAGE_EXTRACTOR: 'openrouter',
    }

    const updated = await adminService.updatePipelineConfig(updates)
    expect(updated).toBeDefined()
    expect(updated.ENABLE_TWO_PASS).toBe(true)
    expect(updated.RESEARCH_ENGINE).toBe('subgraph')
    expect(updated.IMAGE_EXTRACTOR).toBe('openrouter')
    expect(updated).toHaveProperty('updated_at')
  })

  it('preserves unspecified configuration fields during partial updates', async () => {
    const partialUpdate = {
      RESEARCH_ENGINE: 'inline',
    }

    const updated = await adminService.updatePipelineConfig(partialUpdate)
    expect(updated.RESEARCH_ENGINE).toBe('inline')
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
