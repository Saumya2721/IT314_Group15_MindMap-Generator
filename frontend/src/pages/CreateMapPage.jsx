import React, { useState, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMindMap } from '../context/MindMapContext'
import { mindMapService } from '../services/mindMapService'
import { AppHeader } from '../components/layout/AppHeader'
import { AppFooter } from '../components/layout/AppFooter'
import './CreateMapPage.css'

export default function CreateMapPage() {
  const navigate = useNavigate()
  const { draftMap, setDraftMap } = useMindMap()

  // Form states initialized blank or with active draft
  const [title, setTitle] = useState(draftMap?.title || '')
  const [topic, setTopic] = useState(draftMap?.topic || '')
  const [thoughts, setThoughts] = useState(draftMap?.thoughts || '')
  const [detailLevel, setDetailLevel] = useState(draftMap?.detailLevel || 'overview')
  const [attachedFiles, setAttachedFiles] = useState(draftMap?.filePreviews || [])
  const [rawFiles, setRawFiles] = useState(draftMap?.files || [])
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const fileInputRef = useRef(null)

  // Suggested starting templates
  const suggestions = [
    {
      title: 'Renewable Energy Architecture',
      label: 'Renewable Energy',
      colorClass: 'pill-dot-peach',
      sampleThoughts: `- Photovoltaic generation: Perovskite tandem cells reaching 33.7% laboratory efficiency.
- Decentralized storage architecture: Solid-state battery hubs vs. localized flow batteries for microgrids.
- Grid stabilization: Virtual Power Plants (VPPs) aggregating residential inverter telemetry.
- Supply chain vulnerabilities: Rare earth element extraction ethics, cobalt displacement via sodium-ion chemistry.
- Regulatory bottlenecks: Cross-regional transmission line permitting, interconnection queue stagnation.`,
    },
    {
      title: 'Thesis Outline: Computational Ethics',
      label: 'Computational Ethics',
      colorClass: 'pill-dot-gold',
      sampleThoughts: `- Epistemic agency in autonomous multi-agent systems
- Value alignment through constitutional reinforcement learning
- Algorithmic fairness metrics across non-stationary distributions
- Accountability protocols in black-box neural networks`,
    },
    {
      title: 'Market Penetration Strategy',
      label: 'Market Penetration',
      colorClass: 'pill-dot-cyan',
      sampleThoughts: `- Beachhead market identification and ICP segmentation
- Direct sales flywheel vs product-led expansion motions
- Unit economics: CAC payback period and net revenue retention
- Competitive moats: data network effects and switching friction`,
    },
  ]

  // Live word and character metrics
  const { wordCount, charCount } = useMemo(() => {
    const trimmed = thoughts.trim()
    const words = trimmed ? trimmed.split(/\s+/).filter(Boolean).length : 0
    const chars = thoughts.length
    return { wordCount: words, charCount: chars }
  }, [thoughts])

  // Handle clicking a suggestion chip
  const handleSelectSuggestion = (s) => {
    setTopic(s.title)
    if (!title.trim()) {
      setTitle(s.title)
    }
    if (!thoughts.trim()) {
      setThoughts(s.sampleThoughts)
    }
    if (error) setError('')
  }

  // Handle file attachment selection (stores actual binary File objects)
  const handleFileChange = (e) => {
    const selected = Array.from(e.target.files || [])
    if (selected.length > 0) {
      const newPreviews = selected.map((f) => ({
        name: f.name,
        size: (f.size / 1024).toFixed(1) + ' KB',
        type: f.type,
      }))
      setAttachedFiles((prev) => [...prev, ...newPreviews])
      setRawFiles((prev) => [...prev, ...selected])
    }
  }

  // Remove attached file
  const handleRemoveFile = (indexToRemove) => {
    setAttachedFiles((prev) => prev.filter((_, idx) => idx !== indexToRemove))
    setRawFiles((prev) => prev.filter((_, idx) => idx !== indexToRemove))
  }

  // Form submission / Generate Mind Map
  const handleGenerate = async (e) => {
    e.preventDefault()

    const trimmedTopic = topic.trim()
    const trimmedThoughts = thoughts.trim()
    const finalTitle = title.trim() || trimmedTopic || 'Untitled Mind Map'

    // Client-side validation: Topic, ideas, or file required
    if (!trimmedTopic && !trimmedThoughts && rawFiles.length === 0) {
      setError('Please provide a topic, some thoughts, or attach a document for synthesis.')
      return
    }

    setIsSubmitting(true)
    setError('')

    try {
      // Create the map in database upfront exactly once
      const newMap = await mindMapService.createMap({
        title: finalTitle,
      })

      // Save draft data into context
      const updatedDraft = {
        mapId: newMap.id,
        title: finalTitle,
        topic: trimmedTopic || finalTitle,
        thoughts: trimmedThoughts,
        detailLevel,
        files: rawFiles,
        filePreviews: attachedFiles,
      }
      setDraftMap(updatedDraft)

      // Navigate to Generating page with verified mapId
      navigate('/generating', {
        state: updatedDraft,
      })
    } catch (err) {
      setIsSubmitting(false)
      setError(err.message || 'Failed to initialize mind map workspace. Please try again.')
    }
  }

  return (
    <div className="create-map-layout">
      {/* Top Header */}
      <AppHeader />

      {/* Main Form Content */}
      <main className="create-map-main">
        <div className="create-map-content">
          {/* Header Title & Tag */}
          <div className="create-header-section">
            <span className="atelier-badge-tag">Atelier Synthesis</span>
            <h1 className="create-headline">Create a mind map</h1>
            <p className="create-description">
              Start with a thought, a topic, or an unstructured pile of notes.
            </p>
          </div>

          {/* Synthesis Form Card */}
          <form className="create-form-card" onSubmit={handleGenerate} noValidate>
            {/* Field 0: Map Title */}
            <div className="form-field-group">
              <label htmlFor="title-input" className="field-title-label">
                Map Title{' '}
                <span className="field-label-detail">
                  (Optional — generated from topic if empty)
                </span>
              </label>
              <input
                id="title-input"
                type="text"
                className="topic-text-input"
                placeholder="e.g. Renewable Energy Architecture"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>

            {/* Field 1: Topic / Main Idea */}
            <div className="form-field-group">
              <label htmlFor="topic-input" className="field-title-label">
                What's the main idea?
              </label>
              <input
                id="topic-input"
                type="text"
                className={`topic-text-input ${error ? 'has-error' : ''}`}
                placeholder="e.g. Machine Learning, Renaissance Cartography, Carbon Capture..."
                value={topic}
                onChange={(e) => {
                  setTopic(e.target.value)
                  if (error) setError('')
                }}
              />
              {error && <span className="input-error-text">{error}</span>}

              {/* Suggestion Chips */}
              <div className="suggestions-bar">
                <span className="suggestions-prefix">Template suggestions:</span>
                {suggestions.map((s, index) => (
                  <button
                    key={index}
                    type="button"
                    className="suggestion-pill"
                    onClick={() => handleSelectSuggestion(s)}
                  >
                    <span className={`pill-dot ${s.colorClass}`} />
                    <span>{s.label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Field 2: Raw Thoughts Textarea */}
            <div className="form-field-group">
              <label htmlFor="stream-draft" className="field-title-label">
                Add your thoughts
              </label>
              <div className="textarea-surface">
                <textarea
                  id="stream-draft"
                  className="thoughts-input"
                  rows={7}
                  placeholder="Write anything you know, remember, or want to explore... Paste bullet points, transcripts, stream-of-consciousness ideas, or research notes."
                  value={thoughts}
                  onChange={(e) => setThoughts(e.target.value)}
                />
              </div>

              {/* Word & Character Counter */}
              <div className="textarea-metrics-bar">
                <span>
                  {wordCount} {wordCount === 1 ? 'word' : 'words'}
                </span>
                <span>•</span>
                <span>
                  {charCount} {charCount === 1 ? 'character' : 'characters'}
                </span>
              </div>
            </div>

            {/* Detail Level Selection */}
            <div className="form-field-group">
              <label className="field-title-label">Detail Level</label>
              <div style={{ display: 'flex', gap: '12px', marginTop: '4px' }}>
                {[
                  { id: 'overview', label: 'Overview', desc: 'High-level thematic map' },
                  { id: 'balanced', label: 'Balanced', desc: 'Standard breadth & depth' },
                  { id: 'detailed', label: 'Detailed', desc: 'Comprehensive deep dive' },
                ].map((lvl) => (
                  <button
                    key={lvl.id}
                    type="button"
                    onClick={() => setDetailLevel(lvl.id)}
                    style={{
                      flex: 1,
                      padding: '10px 14px',
                      borderRadius: '6px',
                      border:
                        detailLevel === lvl.id
                          ? '2px solid var(--primary)'
                          : '1px solid var(--outline-variant)',
                      backgroundColor:
                        detailLevel === lvl.id
                          ? 'var(--surface-container-low)'
                          : 'var(--surface-container-lowest)',
                      cursor: 'pointer',
                      textAlign: 'left',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 600,
                        color: detailLevel === lvl.id ? 'var(--primary)' : 'var(--on-surface)',
                      }}
                    >
                      {lvl.label}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--outline)', marginTop: '2px' }}>
                      {lvl.desc}
                    </div>
                  </button>
                ))}
              </div>
            </div>

            {/* Field 3: File Attachment Dropzone */}
            <div className="form-field-group">
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                multiple
                accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                onChange={handleFileChange}
              />
              <div
                className="file-attachment-zone"
                onClick={() => fileInputRef.current && fileInputRef.current.click()}
              >
                <div className="dropzone-info-left">
                  <span className="material-symbols-outlined dropzone-file-icon">attach_file</span>
                  <div>
                    <p className="dropzone-heading">
                      Attach source documents <span className="field-label-detail">(Optional)</span>
                    </p>
                    <p className="dropzone-subtext">Drop academic PDFs, images, or notes</p>
                  </div>
                </div>
                <span className="dropzone-browse-trigger">Browse files</span>
              </div>

              {/* List of Attached Files */}
              {attachedFiles.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '6px' }}>
                  {attachedFiles.map((file, idx) => (
                    <div key={idx} className="attached-file-pill">
                      <div className="attached-file-info">
                        <span className="material-symbols-outlined" style={{ fontSize: '15px' }}>
                          description
                        </span>
                        <span>
                          {file.name} ({file.size})
                        </span>
                      </div>
                      <button
                        type="button"
                        className="btn-remove-file"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleRemoveFile(idx)
                        }}
                        aria-label="Remove attachment"
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: '15px' }}>
                          close
                        </span>
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Footer Action & Security Badge */}
            <div className="create-action-footer">
              <p className="security-assurance">
                <span className="material-symbols-outlined security-lock-icon">lock</span>
                <span>Encrypted &amp; private drafting session</span>
              </p>

              <button type="submit" className="btn-generate-submit" disabled={isSubmitting}>
                <span>{isSubmitting ? 'Preparing Synthesis...' : 'Generate mind map'}</span>
                <span className="material-symbols-outlined btn-generate-arrow">arrow_forward</span>
              </button>
            </div>
          </form>
        </div>
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  )
}
