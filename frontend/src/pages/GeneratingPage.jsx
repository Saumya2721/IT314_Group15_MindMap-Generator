import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useLocation, useParams } from 'react-router-dom'
import { useMindMap } from '../context/MindMapContext'
import { mindMapService } from '../services/mindMapService'
import { AppHeader } from '../components/layout/AppHeader'
import { AppFooter } from '../components/layout/AppFooter'
import { ArchivalStepper } from '../components/generator/ArchivalStepper'
import { CanvasTopBar } from '../components/canvas/CanvasTopBar'
import { MindMapCanvas } from '../components/canvas/MindMapCanvas'
import { ExportModal } from '../components/canvas/ExportModal'
import { exportMindMap } from '../services/exportService'
import './GeneratingPage.css'

export default function GeneratingPage({ initialPhase }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { id: routeMapId } = useParams()
  const { draftMap, updateMap } = useMindMap()

  // Determine initial entry phase
  const isDirectEditor =
    initialPhase === 'editor' || location.state?.mode === 'editor' || Boolean(routeMapId)

  // Active Map and Job Tracking
  const [mapId, setMapId] = useState(routeMapId || location.state?.mapId || null)
  const [jobId, setJobId] = useState(location.state?.jobId || null)
  const [isLoadingMap, setIsLoadingMap] = useState(Boolean(routeMapId))
  const [isRegeneration, setIsRegeneration] = useState(Boolean(location.state?.isRegeneration))
  const isRegenerationRef = useRef(Boolean(location.state?.isRegeneration))

  // Operation State Machine: 'initial_generate' | 'regenerating' | 'polling' | 'viewing' | 'error'
  const [operationState, setOperationState] = useState(
    isDirectEditor ? 'viewing' : location.state?.jobId ? 'polling' : 'initial_generate',
  )

  // Phase state: 'generating' or 'editor'
  const [phase, setPhase] = useState(isDirectEditor ? 'editor' : 'generating')

  // Topic, title, thoughts from route state, context, or defaults
  const [mapTitle, setMapTitle] = useState(
    location.state?.title ||
      location.state?.topic ||
      draftMap?.title ||
      draftMap?.topic ||
      'Mind Map',
  )
  const topic = location.state?.topic || draftMap?.topic || mapTitle
  const thoughts = location.state?.thoughts || draftMap?.thoughts || ''
  const detailLevel = location.state?.detailLevel || draftMap?.detailLevel || 'overview'
  const files = location.state?.files || []

  // Graph state (nodes & edges directly from backend)
  const [graphNodes, setGraphNodes] = useState([])
  const [rawEdges, setRawEdges] = useState([])

  // Generation status & progress
  const [progress, setProgress] = useState(15)
  const [currentStep, setCurrentStep] = useState(1)
  const [generationError, setGenerationError] = useState('')
  const [isCancelling, setIsCancelling] = useState(false)
  const [isLeaving, setIsLeaving] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  // View mode switcher: 'canvas' or 'outline'
  const [viewMode, setViewMode] = useState('canvas')

  // Regeneration modal state
  const [showRegenModal, setShowRegenModal] = useState(false)
  const [regenInstruction, setRegenInstruction] = useState('')
  const [regenDetailLevel, setRegenDetailLevel] = useState('overview')

  // Export modal state (S2-FE6)
  const [showExportModal, setShowExportModal] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [exportProgress, setExportProgress] = useState(0)
  const [exportError, setExportError] = useState('')
  const flowApiRef = useRef(null)

  // Toast feedback
  const [toastMessage, setToastMessage] = useState('')
  const toastTimeoutRef = useRef(null)
  const pollTimerRef = useRef(null)
  const pollStartTimestampRef = useRef(null)
  const pollErrorCountRef = useRef(0)
  const generationDispatchedRef = useRef(false)
  const polledJobIdRef = useRef(null)

  const showToast = (msg) => {
    setToastMessage(msg)
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    toastTimeoutRef.current = setTimeout(() => setToastMessage(''), 3500)
  }

  // Clean up timers on unmount
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
      if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    }
  }, [])

  // Bounded exponential backoff polling (1s up to 2.5s, 300s timeout)
  const startJobPolling = useCallback((targetMapId, targetJobId) => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    pollStartTimestampRef.current = Date.now()
    pollErrorCountRef.current = 0

    let delay = 1000

    const poll = async () => {
      // 300s (5-minute) timeout safety
      if (Date.now() - pollStartTimestampRef.current > 300000) {
        setGenerationError(
          'Generation is taking longer than usual. The worker is still processing in the background. You can check its progress on your dashboard.',
        )
        setOperationState('error')
        return
      }

      try {
        const job = await mindMapService.pollJobStatus(targetMapId, targetJobId)
        pollErrorCountRef.current = 0

        const percent = Number(job.progress_percent) || 0
        setProgress(Math.max(percent, 20))

        // Update stepper milestones based on real progress
        if (percent < 25) {
          setCurrentStep(1)
        } else if (percent < 55) {
          setCurrentStep(2)
        } else if (percent < 90) {
          setCurrentStep(3)
        } else {
          setCurrentStep(4)
        }

        // Terminal states
        if (job.status === 'completed') {
          setProgress(100)
          setCurrentStep(4)

          // Fetch freshly generated graph
          const [graphData, mapData] = await Promise.all([
            mindMapService.getMapGraph(targetMapId),
            mindMapService.getMapById(targetMapId).catch(() => null),
          ])

          if (mapData?.title) {
            setMapTitle(mapData.title)
          }
          setGraphNodes(graphData?.nodes || [])
          setRawEdges(graphData?.edges || [])

          setTimeout(() => {
            setPhase('editor')
            setOperationState('viewing')
            setIsLoadingMap(false)
            showToast('Topological synthesis complete. Workspace ready.')
            navigate(`/editor/${targetMapId}`, { replace: true, state: { mode: 'editor' } })
          }, 600)
          return
        }

        if (job.status === 'failed' || job.status === 'rejected') {
          const isRegenMode = isRegenerationRef.current || graphNodes.length > 0
          if (isRegenMode) {
            // CRITICAL (P0): NEVER delete the map or its graph on regeneration failure!
            setGenerationError(
              job.error_message ||
                'Regeneration could not be completed. Your existing mind map has been preserved.',
            )
            setOperationState('error')
            return
          }

          // Automatic cleanup ONLY for uncompleted empty initial drafts
          await mindMapService.deleteMap(targetMapId).catch(() => null)
          setGenerationError(
            job.error_message ||
              'Generation could not be completed. The empty draft has been cleaned up.',
          )
          setOperationState('error')
          return
        }

        // Schedule next poll with slight backoff
        delay = Math.min(delay + 250, 2500)
        pollTimerRef.current = setTimeout(poll, delay)
      } catch (pollErr) {
        pollErrorCountRef.current += 1
        console.warn(`Polling notice (attempt ${pollErrorCountRef.current}):`, pollErr)

        if (pollErrorCountRef.current >= 6) {
          setGenerationError(
            'Lost connection to synthesis server. Please check your network or check your dashboard.',
          )
          setOperationState('error')
          return
        }

        delay = Math.min(delay * 1.5, 3000)
        pollTimerRef.current = setTimeout(poll, delay)
      }
    }

    pollTimerRef.current = setTimeout(poll, delay)
  }, [])

  // Load existing map if opened via /editor/:id
  useEffect(() => {
    if (routeMapId) {
      setMapId(routeMapId)
      setIsLoadingMap(true)

      const loadMapData = async () => {
        try {
          const mapData = await mindMapService.getMapById(routeMapId)
          if (mapData?.title) {
            setMapTitle(mapData.title)
          }

          // If the map is currently being generated, seamlessly resume polling!
          if (
            mapData?.latest_job_status === 'running' ||
            mapData?.latest_job_status === 'pending'
          ) {
            setJobId(mapData.latest_job_id)
            setPhase('generating')
            setOperationState('polling')
            setIsLoadingMap(false)
            startJobPolling(routeMapId, mapData.latest_job_id)
            return
          }

          // Otherwise load graph nodes and edges
          const graphData = await mindMapService.getMapGraph(routeMapId)
          setGraphNodes(graphData?.nodes || [])
          setRawEdges(graphData?.edges || [])
          setPhase('editor')
          setOperationState('viewing')
        } catch (err) {
          console.error('Failed to load map graph:', err)
          showToast(`Error loading map: ${err.message}`)
        } finally {
          setIsLoadingMap(false)
        }
      }
      loadMapData()
    }
  }, [routeMapId, startJobPolling])

  // Initial generation effect (strictly guarded against duplicate execution)
  useEffect(() => {
    if (isDirectEditor || routeMapId) return

    // If navigated with existing jobId (e.g. from Dashboard click on generating card)
    if (location.state?.jobId && location.state?.mapId) {
      if (polledJobIdRef.current === location.state.jobId) return
      polledJobIdRef.current = location.state.jobId
      setPhase('generating')
      setOperationState('polling')
      startJobPolling(location.state.mapId, location.state.jobId)
      return
    }

    if (operationState !== 'initial_generate') return
    if (generationDispatchedRef.current) return
    generationDispatchedRef.current = true

    const dispatchInitialGeneration = async () => {
      setGenerationError('')
      setProgress(15)
      setCurrentStep(1)

      const activeMapId = mapId || location.state?.mapId
      if (!activeMapId) {
        setGenerationError('No target mind map ID provided. Please return to Create.')
        setOperationState('error')
        return
      }

      try {
        const genRes = await mindMapService.generateMap(activeMapId, {
          topic: topic || mapTitle,
          ideas: thoughts,
          detail_level: detailLevel,
          files,
        })

        setJobId(genRes.job_id)
        setOperationState('polling')
        startJobPolling(activeMapId, genRes.job_id)
      } catch (err) {
        // Automatically clean up empty map on immediate dispatch failure
        await mindMapService.deleteMap(activeMapId).catch(() => null)
        setGenerationError(
          err.message ||
            'Unable to start synthesis. The empty draft was removed. Please try again.',
        )
        setOperationState('error')
      }
    }

    dispatchInitialGeneration()
  }, [
    isDirectEditor,
    routeMapId,
    operationState,
    mapId,
    location.state,
    mapTitle,
    topic,
    thoughts,
    detailLevel,
    files,
    startJobPolling,
  ])

  // Handle map title blur to auto-save to backend
  const handleTitleBlur = async () => {
    if (!mapId || !mapTitle.trim()) return
    setIsSaving(true)
    try {
      await updateMap(mapId, { title: mapTitle.trim() })
    } catch (err) {
      console.warn('Failed to auto-save title:', err)
    } finally {
      setIsSaving(false)
    }
  }

  // Cancel Generation handler
  // - For regeneration: non-destructive stop; restores previous canvas view.
  // - For initial generation: deletes the empty uncompleted draft.
  const handleCancelGeneration = async () => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)

    const isRegenMode = isRegeneration || isRegenerationRef.current || graphNodes.length > 0
    if (isRegenMode) {
      // Non-destructive cancel: preserve existing map and return to editor
      setPhase('editor')
      setOperationState('viewing')
      setGenerationError('')
      showToast('Regeneration stopped. Existing mind map preserved.')
      const activeId = mapId || routeMapId
      if (activeId) {
        navigate(`/editor/${activeId}`, { replace: true, state: { mode: 'editor' } })
      }
      return
    }

    setIsCancelling(true)
    try {
      if (mapId) {
        await mindMapService.deleteMap(mapId)
      }
    } catch (err) {
      console.warn('Failed to delete cancelled map:', err)
    }

    navigate('/create')
  }

  // Leave generation: allows ARQ worker to continue in the background
  const handleLeaveGeneration = () => {
    setIsLeaving(true)
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    navigate('/dashboard')
  }

  // Full Map Regeneration handler
  const handleConfirmRegenerate = async (e) => {
    e.preventDefault()
    if (!regenInstruction.trim() || !mapId) return

    setShowRegenModal(false)
    setIsRegeneration(true)
    isRegenerationRef.current = true
    setPhase('generating')
    setOperationState('regenerating')
    setProgress(15)
    setCurrentStep(1)
    setGenerationError('')

    try {
      const res = await mindMapService.regenerateMap(mapId, {
        instruction: regenInstruction.trim(),
        detail_level: regenDetailLevel,
      })
      setJobId(res.job_id)
      setOperationState('polling')
      startJobPolling(mapId, res.job_id)
    } catch (err) {
      setGenerationError(
        err.message || 'Failed to trigger map regeneration. Your previous mind map is preserved.',
      )
      setOperationState('error')
    }
  }

  // Export handler: opens the PDF / PNG export dialog (S2-FE6 / US-07)
  const handleExport = () => {
    setExportError('')
    setExportProgress(0)
    setShowExportModal(true)
  }

  // Export confirmation: rasterizes the map and downloads the requested file
  const handleExportConfirm = async (options = {}) => {
    setIsExporting(true)
    setExportError('')
    setExportProgress(0)

    try {
      const result = await exportMindMap({
        mapTitle,
        options,
        flowApi: flowApiRef.current,
        onProgress: setExportProgress,
      })

      setShowExportModal(false)
      setIsExporting(false)
      setExportProgress(0)
      showToast(`${result.format.toUpperCase()} exported successfully.`)
    } catch (err) {
      setIsExporting(false)
      setExportProgress(0)
      setExportError(err?.message || 'Export failed. Please try again.')
    }
  }

  // Close export modal from within the dialog
  const handleExportModalClose = () => {
    if (isExporting) return
    setShowExportModal(false)
    setExportError('')
    setExportProgress(0)
  }

  // Share handler
  const handleShare = () => {
    navigator.clipboard?.writeText(window.location.href)
    showToast('Shareable workspace link copied to clipboard.')
  }

  // =========================================================
  // RENDER LOADING GRAPH SKELETON
  // =========================================================
  if (isLoadingMap) {
    return (
      <div
        className="generating-editor-view"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        <span
          className="material-symbols-outlined step-spin-icon"
          style={{ fontSize: '36px', color: 'var(--primary)' }}
        >
          sync
        </span>
        <p
          style={{
            marginTop: '16px',
            color: 'var(--on-surface-variant)',
            fontFamily: 'var(--font-headline)',
          }}
        >
          Loading knowledge graph...
        </p>
      </div>
    )
  }

  // =========================================================
  // RENDER PHASE 2: GENERATED MIND MAP CANVAS (ON SAME PAGE)
  // =========================================================
  if (phase === 'editor') {
    return (
      <div className="generating-editor-view">
        {/* Top Editor Navigation Bar */}
        <CanvasTopBar
          title={mapTitle}
          onTitleChange={setMapTitle}
          onTitleBlur={handleTitleBlur}
          onBack={() => navigate('/dashboard')}
          onShare={handleShare}
          onExport={handleExport}
          onRegenerate={() => setShowRegenModal(true)}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          isSaving={isSaving}
        />

        {/* Visual Canvas or Outline Mode */}
        {viewMode === 'outline' ? (
          <div className="outline-view-container">
            <div className="outline-card">
              <h2 className="outline-title font-headline">{mapTitle}</h2>
              {graphNodes.length > 0 ? (
                <ul className="outline-list">
                  {graphNodes
                    .filter((n) => n.node_type === 'root')
                    .map((core) => (
                      <li key={core.id}>
                        <strong>{core.label}</strong>
                        {core.description && (
                          <p style={{ margin: '4px 0 10px', color: 'var(--ink-secondary)' }}>
                            {core.description}
                          </p>
                        )}
                        <ul>
                          {graphNodes
                            .filter((n) => n.node_type === 'branch' || n.node_type === 'default')
                            .map((branch) => (
                              <li key={branch.id}>
                                <strong>{branch.label}</strong>
                                {branch.description && (
                                  <span
                                    style={{
                                      color: 'var(--ink-secondary)',
                                      display: 'block',
                                      fontSize: '12px',
                                    }}
                                  >
                                    {branch.description}
                                  </span>
                                )}
                              </li>
                            ))}
                        </ul>
                      </li>
                    ))}
                </ul>
              ) : (
                <p style={{ color: 'var(--outline)' }}>No nodes available in outline view.</p>
              )}
              <button
                type="button"
                className="btn btn-primary"
                style={{ marginTop: '20px' }}
                onClick={() => setViewMode('canvas')}
              >
                Switch to Canvas View
              </button>
            </div>
          </div>
        ) : (
          <MindMapCanvas
            rawNodes={graphNodes}
            rawEdges={rawEdges}
            toastMessage={toastMessage}
            onClearToast={() => setToastMessage('')}
            flowApiRef={flowApiRef}
          />
        )}

        {/* Regeneration Instruction Modal */}
        {showRegenModal && (
          <div
            style={{
              position: 'fixed',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: 'rgba(24, 29, 26, 0.45)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 1000,
            }}
            onClick={() => setShowRegenModal(false)}
          >
            <div
              style={{
                backgroundColor: 'var(--card-surface, #ffffff)',
                border: '1px solid var(--structural-border, #e1e3e0)',
                borderRadius: '8px',
                padding: '24px',
                maxWidth: '480px',
                width: '90%',
                boxShadow: '0 8px 30px rgba(0, 0, 0, 0.12)',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <h3
                style={{ margin: '0 0 8px', fontFamily: 'var(--font-headline)', fontSize: '20px' }}
              >
                Regenerate Mind Map
              </h3>
              <p
                style={{
                  margin: '0 0 16px',
                  color: 'var(--ink-secondary, #404947)',
                  fontSize: '13px',
                }}
              >
                Provide refined instructions for the synthesis engine to restructure the knowledge
                graph.
              </p>
              <form onSubmit={handleConfirmRegenerate}>
                <textarea
                  rows={4}
                  className="thoughts-input"
                  style={{ width: '100%', marginBottom: '12px', boxSizing: 'border-box' }}
                  placeholder="e.g. Expand on supply chain vulnerabilities and remove introductory overviews..."
                  value={regenInstruction}
                  onChange={(e) => setRegenInstruction(e.target.value)}
                  required
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setShowRegenModal(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={!regenInstruction.trim()}
                  >
                    Regenerate Graph
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Export Mind Map Modal (S2-FE6 / US-07) */}
        {showExportModal && (
          <ExportModal
            mapTitle={mapTitle}
            onClose={handleExportModalClose}
            onExport={handleExportConfirm}
            isExporting={isExporting}
            progress={exportProgress}
            errorMessage={exportError}
          />
        )}
      </div>
    )
  }

  // =========================================================
  // RENDER PHASE 1: GENERATING / SYNTHESIS IN PROGRESS
  // =========================================================
  return (
    <div className="generating-layout">
      {/* Top Header */}
      <AppHeader />

      {/* Main Synthesis Console Area */}
      <main className="generating-main">
        <div className="synthesis-console-card">
          <div className="console-watermark-line" />

          {/* Header Section */}
          <div className="console-header">
            <div className="topic-badge-pill">
              <span className="topic-dot-marker" />
              <span>Topic: {mapTitle}</span>
            </div>
            <h1 className="console-title">Making sense of your ideas</h1>
            <p className="console-subtitle">
              Deconstructing text, synthesizing core concepts, and tracing logical branches with
              deliberate topological clarity.
            </p>
          </div>

          {/* Archival Stepper reflecting real progress */}
          <ArchivalStepper currentStep={currentStep} progressPercent={progress} />

          {/* Generation Error Display */}
          {generationError ? (
            <div
              style={{
                margin: '24px 0',
                padding: '16px',
                backgroundColor: '#fff5f5',
                border: '1px solid #feb2b2',
                borderRadius: '6px',
                color: '#c53030',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>
                  error
                </span>
                <span>{generationError}</span>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                {isRegeneration || graphNodes.length > 0 ? (
                  <>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => {
                        setPhase('editor')
                        setOperationState('viewing')
                        setGenerationError('')
                        const activeId = mapId || routeMapId
                        if (activeId) {
                          navigate(`/editor/${activeId}`, {
                            replace: true,
                            state: { mode: 'editor' },
                          })
                        }
                      }}
                      style={{ fontSize: '12px', padding: '6px 12px' }}
                    >
                      Return to Mind Map
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={() => {
                        setGenerationError('')
                        setShowRegenModal(true)
                      }}
                      style={{ fontSize: '12px', padding: '6px 12px' }}
                    >
                      Try Again
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => navigate('/create')}
                    style={{ fontSize: '12px', padding: '6px 12px' }}
                  >
                    Back to Create
                  </button>
                )}
              </div>
            </div>
          ) : (
            /* Spatial Manifestation Preview */
            <div className="manifestation-container">
              <div className="manifestation-header-row">
                <span className="manifestation-label">
                  <span className="manifestation-label-dot" />
                  Cartographic Canvas Manifestation
                </span>
                <span className="manifestation-speed-rate">
                  {progress >= 90 ? 'Finalizing Graph' : 'Queued Worker Active'}
                </span>
              </div>

              <div className="manifestation-viewport">
                <div className="manifestation-grid-bg" />

                <svg className="manifestation-vectors-svg" viewBox="0 0 760 288" fill="none">
                  <path
                    d="M 380 144 C 300 144, 240 76, 170 76"
                    stroke="#155e63"
                    strokeWidth="1.5"
                    strokeDasharray="4 3"
                    className="connector-pulse"
                  />
                  <path
                    d="M 380 144 C 460 144, 520 84, 590 84"
                    stroke="#155e63"
                    strokeWidth="1.5"
                    strokeDasharray="4 3"
                    className="connector-pulse"
                  />
                  <path
                    d="M 380 144 C 440 144, 490 206, 560 206"
                    stroke="#6f7979"
                    strokeWidth="1.5"
                    strokeDasharray="2 3"
                    opacity="0.35"
                  />
                </svg>

                <div className="manifestation-core-node">
                  <span className="core-top-accent" />
                  <span className="core-tag">Root Origin</span>
                  <span className="core-title">{mapTitle}</span>
                </div>

                <div className="manifesting-branch branch-pos-left">
                  <span className="branch-top-strip peach" />
                  <div className="branch-header-row">
                    <span className="pill-dot pill-dot-peach" />
                    <span className="branch-title">Foundational Elements</span>
                  </div>
                  <span className="branch-subtitle">Taxonomy &amp; Core Drivers</span>
                </div>

                <div className="manifesting-branch branch-pos-right">
                  <span className="branch-top-strip teal" />
                  <div className="branch-header-row">
                    <span className="pill-dot pill-dot-cyan" />
                    <span className="branch-title">Applied Synthesis</span>
                  </div>
                  <span className="branch-subtitle">Architecture &amp; Flow</span>
                </div>

                <div className="manifesting-branch branch-pos-ghosted">
                  <div className="branch-header-row">
                    <span className="pill-dot" style={{ backgroundColor: 'var(--outline)' }} />
                    <span className="branch-title" style={{ color: 'var(--outline)' }}>
                      Exploratory Vector
                    </span>
                  </div>
                  <span className="branch-subtitle">Future Trajectories</span>
                </div>

                <div className="spatial-marker-pill">
                  <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                    crop_free
                  </span>
                  <span>Progress: {progress}%</span>
                </div>
              </div>
            </div>
          )}

          {/* Console Footer & Dual Cancel/Leave Actions */}
          <div className="console-footer-bar">
            <div className="contemplative-quote">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: '16px', color: 'var(--primary-container)' }}
              >
                format_quote
              </span>
              <span>Ideas become clearer when given space and relationship.</span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              {/* Action 1: Cancel Generation / Stop Waiting */}
              {isRegeneration || graphNodes.length > 0 ? (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleCancelGeneration}
                  disabled={isCancelling || isLeaving}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                  }}
                  title="Stop waiting for regeneration; your current map is preserved."
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                    arrow_back
                  </span>
                  <span>Stop waiting &amp; return to map</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleCancelGeneration}
                  disabled={isCancelling || isLeaving}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '6px',
                    color: '#ba1a1a',
                    borderColor: '#ffdad6',
                    backgroundColor: '#fff8f7',
                  }}
                  title="Cancel synthesis and remove the draft from database"
                >
                  {isCancelling ? (
                    <>
                      <span
                        className="material-symbols-outlined step-spin-icon"
                        style={{ fontSize: '14px' }}
                      >
                        sync
                      </span>
                      <span>Cancelling &amp; removing...</span>
                    </>
                  ) : (
                    <>
                      <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                        delete_outline
                      </span>
                      <span>Cancel draft</span>
                    </>
                  )}
                </button>
              )}

              {/* Action 2: Run in background / Leave generation */}
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleLeaveGeneration}
                disabled={isCancelling || isLeaving}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
                title="Leave this screen; worker processing continues in the background"
              >
                {isLeaving ? (
                  <>
                    <span
                      className="material-symbols-outlined step-spin-icon"
                      style={{ fontSize: '14px' }}
                    >
                      sync
                    </span>
                    <span>Navigating...</span>
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                      arrow_outward
                    </span>
                    <span>Run in background</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Diagnostics Subpanel */}
        <div className="diagnostics-panel">
          <div className="diagnostics-left">
            <span className="diagnostics-dot" />
            <span>ARQ Worker Engine / Graph Hierarchy Generator</span>
          </div>
          <div className="diagnostics-latency">LIVE PIPELINE</div>
        </div>
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  )
}
