import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { adminService } from '../../services/adminService'
import { AppHeader } from '../../components/layout/AppHeader'
import { AppFooter } from '../../components/layout/AppFooter'
import { AdminErrorBoundary } from './AdminErrorBoundary'
import './AdminPipelineConfigPage.css'

export function AdminPipelineConfigContent() {
  const [config, setConfig] = useState(null)
  const [initialConfig, setInitialConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [statusMessage, setStatusMessage] = useState(null) // { type: 'success' | 'error', text: '' }

  const loadConfig = useCallback(async () => {
    setLoading(true)
    setStatusMessage(null)
    try {
      const data = await adminService.getPipelineConfig()
      setConfig(data)
      setInitialConfig(data)
    } catch (err) {
      console.error('Failed to load pipeline configuration:', err)
      setStatusMessage({
        type: 'error',
        text: 'Failed to load pipeline settings. Using fallback values.',
      })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

  const handleToggleTwoPass = (e) => {
    const checked = e.target.checked
    setConfig((prev) => ({
      ...prev,
      ENABLE_TWO_PASS: checked,
    }))
  }

  const handleSelectResearchEngine = (engine) => {
    setConfig((prev) => ({
      ...prev,
      RESEARCH_ENGINE: engine,
    }))
  }

  const handleSelectImageExtractor = (extractor) => {
    setConfig((prev) => ({
      ...prev,
      IMAGE_EXTRACTOR: extractor,
    }))
  }

  const handleReset = () => {
    if (initialConfig) {
      setConfig({ ...initialConfig })
      setStatusMessage(null)
    }
  }

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    setStatusMessage(null)
    try {
      const updates = {
        ENABLE_TWO_PASS: Boolean(config.ENABLE_TWO_PASS),
        RESEARCH_ENGINE: config.RESEARCH_ENGINE || 'inline',
        IMAGE_EXTRACTOR: config.IMAGE_EXTRACTOR || 'gemini',
      }
      const updated = await adminService.updatePipelineConfig(updates)
      setConfig((prev) => ({ ...prev, ...updated }))
      setInitialConfig((prev) => ({ ...prev, ...updated }))
      setStatusMessage({
        type: 'success',
        text: 'Pipeline configuration updated and applied successfully.',
      })
    } catch (err) {
      console.error('Failed to save pipeline configuration:', err)
      setStatusMessage({
        type: 'error',
        text: err?.message || 'Error updating pipeline configuration.',
      })
    } finally {
      setSaving(false)
    }
  }

  const hasChanges =
    initialConfig &&
    (config.ENABLE_TWO_PASS !== initialConfig.ENABLE_TWO_PASS ||
      config.RESEARCH_ENGINE !== initialConfig.RESEARCH_ENGINE ||
      config.IMAGE_EXTRACTOR !== initialConfig.IMAGE_EXTRACTOR)

  return (
    <div className="pipeline-config-layout">
      <AppHeader />
      <main className="pipeline-config-main">
        {/* Breadcrumb Navigation & Header */}
        <div className="pipeline-config-header">
          <nav className="pipeline-nav-breadcrumbs" aria-label="Breadcrumb">
            <Link to="/admin" className="pipeline-breadcrumb-link">
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                arrow_back
              </span>
              Admin Dashboard
            </Link>
            <span>/</span>
            <span>Pipeline Configuration</span>
          </nav>
          <h1 className="pipeline-config-title">Pipeline Runtime Settings</h1>
          <p className="pipeline-config-subtitle">
            Configure LLM synthesis behaviors, multimodality image extractors, and live research
            orchestration flags (S3-BE8).
          </p>
        </div>

        {/* Status Notification Banner */}
        {statusMessage && (
          <div
            className={`pipeline-status-banner ${statusMessage.type}`}
            role="alert"
            aria-live="polite"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              {statusMessage.type === 'success' ? 'check_circle' : 'error'}
            </span>
            <span>{statusMessage.text}</span>
          </div>
        )}

        {loading ? (
          <div
            style={{
              padding: '64px',
              textAlign: 'center',
              color: 'var(--ink-secondary, #4a6358)',
            }}
          >
            <span
              className="material-symbols-outlined step-spin-icon"
              style={{ fontSize: '32px', color: 'var(--accent-sage, #2d5a47)' }}
            >
              sync
            </span>
            <p style={{ marginTop: '12px' }}>Loading pipeline feature flags...</p>
          </div>
        ) : (
          <form onSubmit={handleSave} className="pipeline-cards-grid">
            {/* Card 1: Dynamic Feature Flags */}
            <div className="pipeline-card">
              <div className="pipeline-card-header">
                <div className="pipeline-card-title-group">
                  <span className="material-symbols-outlined pipeline-card-icon">toggle_on</span>
                  <div>
                    <h2 className="pipeline-card-title">Runtime Feature Toggles</h2>
                    <p className="pipeline-card-desc">
                      Modify orchestrator behavior dynamically without redeploying workers.
                    </p>
                  </div>
                </div>
              </div>

              <div className="flag-rows-container">
                {/* Flag 1: ENABLE_TWO_PASS */}
                <div className="flag-row">
                  <div className="flag-info">
                    <label htmlFor="toggle-two-pass" className="flag-name">
                      ENABLE_TWO_PASS
                    </label>
                    <p className="flag-description">
                      Enables two-pass synthesis. First pass outlines concept hierarchies; second
                      pass refines relationships and semantic clarity.
                    </p>
                  </div>
                  <label className="toggle-switch">
                    <input
                      id="toggle-two-pass"
                      type="checkbox"
                      checked={Boolean(config?.ENABLE_TWO_PASS)}
                      onChange={handleToggleTwoPass}
                      aria-label="Toggle ENABLE_TWO_PASS"
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>

                {/* Flag 2: RESEARCH_ENGINE */}
                <div className="flag-row">
                  <div className="flag-info">
                    <span className="flag-name">RESEARCH_ENGINE</span>
                    <p className="flag-description">
                      Selects grounding engine architecture. <strong>inline</strong> executes prompt
                      grounding in-thread, while <strong>subgraph</strong> runs autonomous parallel
                      research agents.
                    </p>
                  </div>
                  <div
                    className="pill-selector-group"
                    role="radiogroup"
                    aria-label="Research Engine"
                  >
                    <button
                      type="button"
                      className={`pill-option-btn ${config?.RESEARCH_ENGINE === 'subgraph' ? 'active' : ''}`}
                      onClick={() => handleSelectResearchEngine('subgraph')}
                      role="radio"
                      aria-checked={config?.RESEARCH_ENGINE === 'subgraph'}
                    >
                      subgraph
                    </button>
                    <button
                      type="button"
                      className={`pill-option-btn ${config?.RESEARCH_ENGINE === 'inline' ? 'active' : ''}`}
                      onClick={() => handleSelectResearchEngine('inline')}
                      role="radio"
                      aria-checked={config?.RESEARCH_ENGINE === 'inline'}
                    >
                      inline
                    </button>
                  </div>
                </div>

                {/* Flag 3: IMAGE_EXTRACTOR */}
                <div className="flag-row">
                  <div className="flag-info">
                    <span className="flag-name">IMAGE_EXTRACTOR</span>
                    <p className="flag-description">
                      Engine responsible for OCR and visual diagram ingestion from uploaded assets.
                    </p>
                  </div>
                  <div
                    className="pill-selector-group"
                    role="radiogroup"
                    aria-label="Image Extractor"
                  >
                    {['gemini', 'openrouter', 'tesseract'].map((engine) => (
                      <button
                        key={engine}
                        type="button"
                        className={`pill-option-btn ${config?.IMAGE_EXTRACTOR === engine ? 'active' : ''}`}
                        onClick={() => handleSelectImageExtractor(engine)}
                        role="radio"
                        aria-checked={config?.IMAGE_EXTRACTOR === engine}
                      >
                        {engine}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Card 2: Active Prompt Templates & LLM Provider Metadata */}
            <div className="pipeline-card">
              <div className="pipeline-card-header">
                <div className="pipeline-card-title-group">
                  <span className="material-symbols-outlined pipeline-card-icon">psychology</span>
                  <div>
                    <h2 className="pipeline-card-title">LLM Model &amp; Prompt Metadata</h2>
                    <p className="pipeline-card-desc">
                      Current upstream synthesis orchestrator and prompt template versions.
                    </p>
                  </div>
                </div>
              </div>

              <div className="info-pairs-grid">
                <div className="info-pair-box">
                  <div className="info-pair-label">Active Provider</div>
                  <div className="info-pair-value">
                    {config?.llm?.provider || 'groq'}
                    <span className="info-pair-badge">Active</span>
                  </div>
                </div>
                <div className="info-pair-box">
                  <div className="info-pair-label">Base Model</div>
                  <div className="info-pair-value">
                    {config?.llm?.model || 'llama-3.3-70b-versatile'}
                  </div>
                </div>
                <div className="info-pair-box">
                  <div className="info-pair-label">Prompt Template Version</div>
                  <div className="info-pair-value">{config?.prompts?.version || 'v1.0'}</div>
                </div>
                <div className="info-pair-box">
                  <div className="info-pair-label">Temperature</div>
                  <div className="info-pair-value">
                    {config?.llm?.temperature !== undefined ? config.llm.temperature : 0.2}
                  </div>
                </div>
              </div>
            </div>

            {/* Footer Action Buttons */}
            <div className="pipeline-footer-bar">
              <button
                type="button"
                className="btn-pipeline-reset"
                onClick={handleReset}
                disabled={!hasChanges || saving}
              >
                Reset Changes
              </button>
              <button type="submit" className="btn-pipeline-save" disabled={!hasChanges || saving}>
                {saving ? (
                  <>
                    <span
                      className="material-symbols-outlined step-spin-icon"
                      style={{ fontSize: '16px' }}
                    >
                      sync
                    </span>
                    Saving Configuration...
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                      save
                    </span>
                    Apply Configuration
                  </>
                )}
              </button>
            </div>
          </form>
        )}
      </main>
      <AppFooter />
    </div>
  )
}

export default function AdminPipelineConfigPage() {
  return (
    <AdminErrorBoundary>
      <AdminPipelineConfigContent />
    </AdminErrorBoundary>
  )
}
