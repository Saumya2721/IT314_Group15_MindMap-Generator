import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { adminService } from '../../services/adminService'
import { AppHeader } from '../../components/layout/AppHeader'
import { AppFooter } from '../../components/layout/AppFooter'
import { AdminErrorBoundary } from './AdminErrorBoundary'
import './AdminPipelineConfigPage.css'

const MODEL_PRESETS = [
  'openai/gpt-oss-20b',
  'llama-3.3-70b-versatile',
  'qwen/qwen3.8-27b',
  'gpt-4o-mini',
  'gpt-4o',
]

export function AdminPipelineConfigContent() {
  const [config, setConfig] = useState(null)
  const [initialConfig, setInitialConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [statusMessage, setStatusMessage] = useState(null) // { type: 'success' | 'error', text: '' }

  const normalizeConfig = (data) => {
    if (!data) return null
    return {
      enable_two_pass: data.enable_two_pass ?? data.ENABLE_TWO_PASS ?? false,
      research_engine: data.research_engine ?? data.RESEARCH_ENGINE ?? 'subgraph',
      image_extractor: data.image_extractor ?? data.IMAGE_EXTRACTOR ?? 'gemini',
      prompt_template_version: data.prompt_template_version ?? 'v1.0',
      llm_provider: data.llm_provider ?? data.llm?.provider ?? 'groq',
      llm_model: data.llm_model ?? data.llm?.model ?? 'openai/gpt-oss-20b',
      temperature:
        data.temperature ?? data.llm?.temperature ?? data.llm?.hyperparameters?.temperature ?? 0.2,
      max_output_tokens:
        data.max_output_tokens ?? data.llm?.hyperparameters?.max_output_tokens ?? 8000,
      langsmith_tracing: data.langsmith_tracing ?? data.flags?.langsmith_tracing ?? false,
    }
  }

  const loadConfig = useCallback(async () => {
    setLoading(true)
    setStatusMessage(null)
    try {
      const data = await adminService.getPipelineConfig()
      const norm = normalizeConfig(data)
      setConfig(norm)
      setInitialConfig(norm)
    } catch (err) {
      console.error('Failed to load pipeline configuration:', err)
      setStatusMessage({
        type: 'error',
        text: 'Failed to load pipeline settings.',
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
      enable_two_pass: checked,
    }))
  }

  const handleToggleLangsmith = (e) => {
    const checked = e.target.checked
    setConfig((prev) => ({
      ...prev,
      langsmith_tracing: checked,
    }))
  }

  const handleSelectResearchEngine = (engine) => {
    setConfig((prev) => ({
      ...prev,
      research_engine: engine,
    }))
  }

  const handleSelectImageExtractor = (extractor) => {
    setConfig((prev) => ({
      ...prev,
      image_extractor: extractor,
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
        enable_two_pass: Boolean(config.enable_two_pass),
        research_engine: config.research_engine || 'subgraph',
        image_extractor: config.image_extractor || 'gemini',
        llm_provider: config.llm_provider || 'groq',
        llm_model: config.llm_model || 'openai/gpt-oss-20b',
        temperature: parseFloat(config.temperature ?? 0.2),
        max_output_tokens: parseInt(config.max_output_tokens ?? 8000, 10),
        langsmith_tracing: Boolean(config.langsmith_tracing),
      }
      const updated = await adminService.updatePipelineConfig(updates)
      const norm = normalizeConfig(updated)
      setConfig((prev) => ({ ...prev, ...norm }))
      setInitialConfig((prev) => ({ ...prev, ...norm }))
      setStatusMessage({
        type: 'success',
        text: 'Pipeline settings saved successfully. Workers pick up changes automatically.',
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
    (config.enable_two_pass !== initialConfig.enable_two_pass ||
      config.research_engine !== initialConfig.research_engine ||
      config.image_extractor !== initialConfig.image_extractor ||
      config.llm_provider !== initialConfig.llm_provider ||
      config.llm_model !== initialConfig.llm_model ||
      Number(config.temperature) !== Number(initialConfig.temperature) ||
      Number(config.max_output_tokens) !== Number(initialConfig.max_output_tokens) ||
      config.langsmith_tracing !== initialConfig.langsmith_tracing)

  return (
    <div className="pipeline-config-layout">
      <AppHeader />
      <main className="pipeline-config-main">
        {/* Header with Back Button and Subnav Tabs */}
        <div className="admin-header">
          <div className="admin-header-top">
            <Link to="/admin" className="admin-back-btn">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                arrow_back
              </span>
              <span>Back to Overview</span>
            </Link>
            <span className="admin-badge">
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                shield_person
              </span>
              Platform Administration
            </span>
          </div>

          <div className="admin-subnav-tabs">
            <Link to="/admin" className="admin-subnav-tab">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                dashboard
              </span>
              Overview &amp; Analytics
            </Link>
            <Link to="/admin/config" className="admin-subnav-tab active">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                tune
              </span>
              Pipeline Runtime Settings
            </Link>
          </div>

          <h1 className="admin-title">Pipeline Runtime Settings</h1>
          <p className="admin-subtitle">
            Configure dynamic LLM providers, model checkpoints, temperatures, LangSmith tracing, and
            research orchestration engines in real time without restarting workers.
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
              color: 'var(--ink-secondary, #a3aca9)',
            }}
          >
            <span
              className="material-symbols-outlined step-spin-icon"
              style={{ fontSize: '32px', color: 'var(--accent-sage, #899b72)' }}
            >
              sync
            </span>
            <p style={{ marginTop: '12px' }}>Loading pipeline feature flags...</p>
          </div>
        ) : (
          <form onSubmit={handleSave} className="pipeline-cards-grid">
            {/* Card 1: LLM Provider, Model & Hyperparameters */}
            <div className="pipeline-card">
              <div className="pipeline-card-header">
                <div className="pipeline-card-title-group">
                  <span className="material-symbols-outlined pipeline-card-icon">psychology</span>
                  <div>
                    <h2 className="pipeline-card-title">LLM Provider &amp; Model Selection</h2>
                    <p className="pipeline-card-desc">
                      Configure the active inference provider, model target, and generation
                      hyperparameters.
                    </p>
                  </div>
                </div>
              </div>

              <div className="pipeline-form-grid">
                {/* Field 1: Active Provider */}
                <div className="pipeline-field-group">
                  <label htmlFor="pipeline-provider" className="pipeline-field-label">
                    Active Provider
                  </label>
                  <select
                    id="pipeline-provider"
                    className="pipeline-select"
                    value={config?.llm_provider || 'groq'}
                    onChange={(e) =>
                      setConfig((prev) => ({ ...prev, llm_provider: e.target.value }))
                    }
                  >
                    <option value="groq">Groq (Ultra-Fast Inference)</option>
                    <option value="openai">OpenAI (GPT-4o / GPT-4o-mini)</option>
                    <option value="mock">Deterministic Mock (Testing)</option>
                  </select>
                </div>

                {/* Field 2: Base Model */}
                <div className="pipeline-field-group">
                  <label htmlFor="pipeline-model" className="pipeline-field-label">
                    Base Model Target
                  </label>
                  <input
                    id="pipeline-model"
                    type="text"
                    list="model-options"
                    className="pipeline-input"
                    value={config?.llm_model || ''}
                    onChange={(e) => setConfig((prev) => ({ ...prev, llm_model: e.target.value }))}
                    placeholder="e.g. openai/gpt-oss-20b or llama-3.3-70b-versatile"
                  />
                  <datalist id="model-options">
                    {MODEL_PRESETS.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                </div>

                {/* Field 3: Temperature */}
                <div className="pipeline-field-group">
                  <label htmlFor="pipeline-temp" className="pipeline-field-label">
                    Temperature (Creativity)
                  </label>
                  <div className="pipeline-slider-wrap">
                    <input
                      id="pipeline-temp"
                      type="range"
                      min="0.0"
                      max="1.0"
                      step="0.05"
                      className="pipeline-slider"
                      value={config?.temperature ?? 0.2}
                      onChange={(e) =>
                        setConfig((prev) => ({ ...prev, temperature: parseFloat(e.target.value) }))
                      }
                    />
                    <span className="pipeline-slider-val">
                      {Number(config?.temperature ?? 0.2).toFixed(2)}
                    </span>
                  </div>
                </div>

                {/* Field 4: Max Output Tokens */}
                <div className="pipeline-field-group">
                  <label htmlFor="pipeline-tokens" className="pipeline-field-label">
                    Max Output Tokens
                  </label>
                  <input
                    id="pipeline-tokens"
                    type="number"
                    min="1000"
                    max="8000"
                    step="500"
                    className="pipeline-input"
                    value={config?.max_output_tokens ?? 8000}
                    onChange={(e) =>
                      setConfig((prev) => ({
                        ...prev,
                        max_output_tokens: parseInt(e.target.value, 10),
                      }))
                    }
                  />
                </div>
              </div>
            </div>

            {/* Card 2: Observability & Tracing */}
            <div className="pipeline-card">
              <div className="pipeline-card-header">
                <div className="pipeline-card-title-group">
                  <span className="material-symbols-outlined pipeline-card-icon">visibility</span>
                  <div>
                    <h2 className="pipeline-card-title">LangSmith Observability &amp; Tracing</h2>
                    <p className="pipeline-card-desc">
                      Telemetry, execution trace capture, and quality-gate evaluation
                      instrumentation (S3-OBS).
                    </p>
                  </div>
                </div>
              </div>

              <div className="flag-rows-container">
                <div className="flag-row">
                  <div className="flag-info">
                    <label htmlFor="toggle-langsmith" className="flag-name">
                      LANGSMITH_TRACING
                    </label>
                    <p className="flag-description">
                      Stream graph node transitions, research agent telemetry, and execution spans
                      to the LangSmith observability dashboard. When disabled, tracing runs in
                      zero-overhead no-op mode.
                    </p>
                  </div>
                  <label className="toggle-switch">
                    <input
                      id="toggle-langsmith"
                      type="checkbox"
                      checked={Boolean(config?.langsmith_tracing)}
                      onChange={handleToggleLangsmith}
                      aria-label="Toggle LANGSMITH_TRACING"
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>
              </div>
            </div>

            {/* Card 3: Runtime Feature Toggles & Grounding */}
            <div className="pipeline-card">
              <div className="pipeline-card-header">
                <div className="pipeline-card-title-group">
                  <span className="material-symbols-outlined pipeline-card-icon">toggle_on</span>
                  <div>
                    <h2 className="pipeline-card-title">Runtime Feature Toggles &amp; Grounding</h2>
                    <p className="pipeline-card-desc">
                      Modify orchestrator behavior, multimodal extractors, and research grounding
                      engines dynamically.
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
                      checked={Boolean(config?.enable_two_pass)}
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
                      Selects grounding engine architecture. <strong>subgraph</strong> runs
                      autonomous parallel research agents, while <strong>inline</strong> executes
                      prompt grounding in-thread.
                    </p>
                  </div>
                  <div
                    className="pill-selector-group"
                    role="radiogroup"
                    aria-label="Research Engine"
                  >
                    <button
                      type="button"
                      className={`pill-option-btn ${config?.research_engine === 'subgraph' ? 'active' : ''}`}
                      onClick={() => handleSelectResearchEngine('subgraph')}
                      role="radio"
                      aria-checked={config?.research_engine === 'subgraph'}
                    >
                      subgraph
                    </button>
                    <button
                      type="button"
                      className={`pill-option-btn ${config?.research_engine === 'inline' ? 'active' : ''}`}
                      onClick={() => handleSelectResearchEngine('inline')}
                      role="radio"
                      aria-checked={config?.research_engine === 'inline'}
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
                        className={`pill-option-btn ${config?.image_extractor === engine ? 'active' : ''}`}
                        onClick={() => handleSelectImageExtractor(engine)}
                        role="radio"
                        aria-checked={config?.image_extractor === engine}
                      >
                        {engine}
                      </button>
                    ))}
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
