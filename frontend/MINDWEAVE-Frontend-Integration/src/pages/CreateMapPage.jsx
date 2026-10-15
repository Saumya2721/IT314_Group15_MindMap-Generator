import React, { useState, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMindMap } from '../context/MindMapContext';
import { AppHeader } from '../components/layout/AppHeader';
import { AppFooter } from '../components/layout/AppFooter';
import './CreateMapPage.css';

export default function CreateMapPage() {
  const navigate = useNavigate();
  const { draftMap, setDraftMap } = useMindMap();

  // Form states initialized with current draft or Stitch default examples
  const [topic, setTopic] = useState(draftMap?.topic || 'Renewable Energy Architecture');
  const [thoughts, setThoughts] = useState(
    draftMap?.thoughts ||
      `- Photovoltaic generation: Perovskite tandem cells reaching 33.7% laboratory efficiency.
- Decentralized storage architecture: Solid-state battery hubs vs. localized flow batteries for microgrids.
- Grid stabilization: Virtual Power Plants (VPPs) aggregating residential inverter telemetry.
- Supply chain vulnerabilities: Rare earth element extraction ethics, cobalt displacement via sodium-ion chemistry.
- Regulatory bottlenecks: Cross-regional transmission line permitting, interconnection queue stagnation.`
  );
  const [attachedFiles, setAttachedFiles] = useState(draftMap?.files || []);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const fileInputRef = useRef(null);

  // Suggestions from the Stitch mockup
  const suggestions = [
    { title: 'Renewable Energy Architecture', label: 'Renewable Energy', colorClass: 'pill-dot-peach' },
    { title: 'Thesis Outline: Computational Ethics', label: 'Thesis Outline', colorClass: 'pill-dot-gold' },
    { title: 'Market Penetration Strategy', label: 'Market Penetration', colorClass: 'pill-dot-cyan' },
  ];

  // Live word and character metrics
  const { wordCount, charCount } = useMemo(() => {
    const trimmed = thoughts.trim();
    const words = trimmed ? trimmed.split(/\s+/).filter(Boolean).length : 0;
    const chars = thoughts.length;
    return { wordCount: words, charCount: chars };
  }, [thoughts]);

  // Handle clicking a suggestion chip
  const handleSelectSuggestion = (suggestedTitle) => {
    setTopic(suggestedTitle);
    if (error) setError('');
  };

  // Handle file attachment selection
  const handleFileChange = (e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) {
      const newFiles = files.map((f) => ({
        name: f.name,
        size: (f.size / 1024).toFixed(1) + ' KB',
      }));
      setAttachedFiles((prev) => [...prev, ...newFiles]);
    }
  };

  // Remove attached file
  const handleRemoveFile = (indexToRemove) => {
    setAttachedFiles((prev) => prev.filter((_, idx) => idx !== indexToRemove));
  };

  // Form submission / Generate Mind Map
  const handleGenerate = (e) => {
    e.preventDefault();

    // Client-side validation: Topic is required
    if (!topic.trim()) {
      setError('Please provide a topic or central idea for your mind map.');
      return;
    }

    setIsSubmitting(true);

    // Save current draft data into context
    const updatedDraft = {
      topic: topic.trim(),
      thoughts: thoughts.trim(),
      files: attachedFiles,
    };
    setDraftMap(updatedDraft);

    // Navigate to the Generating Map screen with state
    navigate('/generating', {
      state: updatedDraft,
    });
  };

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
                  setTopic(e.target.value);
                  if (error) setError('');
                }}
              />
              {error && <span className="input-error-text">{error}</span>}

              {/* Suggestion Chips */}
              <div className="suggestions-bar">
                <span className="suggestions-prefix">Suggestions:</span>
                {suggestions.map((s, index) => (
                  <button
                    key={index}
                    type="button"
                    className="suggestion-pill"
                    onClick={() => handleSelectSuggestion(s.title)}
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

            {/* Field 3: File Attachment Dropzone */}
            <div className="form-field-group">
              <input
                type="file"
                ref={fileInputRef}
                style={{ display: 'none' }}
                multiple
                accept=".pdf,.txt,.doc,.docx,.md"
                onChange={handleFileChange}
              />
              <div
                className="file-attachment-zone"
                onClick={() => fileInputRef.current && fileInputRef.current.click()}
              >
                <div className="dropzone-info-left">
                  <span className="material-symbols-outlined dropzone-file-icon">
                    attach_file
                  </span>
                  <div>
                    <p className="dropzone-heading">
                      Attach source documents <span className="field-label-detail">(Optional)</span>
                    </p>
                    <p className="dropzone-subtext">Drop academic PDFs, notes, or research papers</p>
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
                          e.stopPropagation();
                          handleRemoveFile(idx);
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

              <button
                type="submit"
                className="btn-generate-submit"
                disabled={isSubmitting}
              >
                <span>{isSubmitting ? 'Preparing Synthesis...' : 'Generate mind map'}</span>
                <span className="material-symbols-outlined btn-generate-arrow">
                  arrow_forward
                </span>
              </button>
            </div>
          </form>
        </div>
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  );
}
