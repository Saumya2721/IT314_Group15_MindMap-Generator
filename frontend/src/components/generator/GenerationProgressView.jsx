import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { mindMapService } from '../../services/mindMapService'
import { AppHeader } from '../layout/AppHeader'
import { AppFooter } from '../layout/AppFooter'
import { ArchivalStepper } from './ArchivalStepper'
import '../../pages/GeneratingPage.css'

/**
 * GenerationProgressView
 *
 * Dedicated component for generation & regeneration progress presentation
 * and ARQ job polling. Extracted from GeneratingPage.jsx as part of Issue #104.
 */
export function GenerationProgressView({
  mapId,
  jobId: initialJobId = null,
  initialParams = null,
  mapTitle = 'Mind Map',
  isRegeneration = false,
  existingNodes = [],
  onComplete = null,
  onCancel = null,
  onError = null,
  fullPage = true,
}) {
  const navigate = useNavigate()

  const [currentJobId, setCurrentJobId] = useState(initialJobId)
  const [progress, setProgress] = useState(15)
  const [currentStep, setCurrentStep] = useState(1)
  const [generationError, setGenerationError] = useState('')
  const [isCancelling, setIsCancelling] = useState(false)
  const [isLeaving, setIsLeaving] = useState(false)

  const isMountedRef = useRef(true)
  const pollTimerRef = useRef(null)
  const pollStartTimestampRef = useRef(null)
  const pollErrorCountRef = useRef(0)
  const completedJobIdRef = useRef(null)
  const isDispatchingRef = useRef(false)

  // Track isRegeneration and existing graph for P0 safety checks
  const isRegenMode = isRegeneration || (Array.isArray(existingNodes) && existingNodes.length > 0)
  const isRegenRef = useRef(isRegenMode)
  useEffect(() => {
    isRegenRef.current = isRegenMode
  }, [isRegenMode])

  // Sync prop changes for initialJobId
  useEffect(() => {
    if (initialJobId && initialJobId !== currentJobId) {
      setCurrentJobId(initialJobId)
    }
  }, [initialJobId, currentJobId])

  // Unmount & timer cleanup guard
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [])

  // Initial generation dispatch (when no jobId is provided upfront)
  useEffect(() => {
    if (currentJobId || !mapId || !initialParams) return
    if (isDispatchingRef.current) return
    isDispatchingRef.current = true

    const dispatch = async () => {
      setGenerationError('')
      setProgress(15)
      setCurrentStep(1)

      try {
        const genRes = await mindMapService.generateMap(mapId, {
          topic: initialParams.topic || mapTitle,
          ideas: initialParams.ideas || '',
          detail_level: initialParams.detail_level || 'overview',
          files: initialParams.files || [],
        })

        if (!isMountedRef.current) return
        setCurrentJobId(genRes.job_id)
      } catch (err) {
        if (!isMountedRef.current) return
        // Automatically clean up empty draft on immediate dispatch failure
        await mindMapService.deleteMap(mapId).catch(() => null)
        const msg =
          err.message || 'Unable to start synthesis. The empty draft was removed. Please try again.'
        setGenerationError(msg)
        if (onError) onError(msg)
      } finally {
        isDispatchingRef.current = false
      }
    }

    dispatch()
  }, [mapId, currentJobId, initialParams, mapTitle, onError])

  // Bounded exponential backoff polling (React StrictMode & unmount safe)
  useEffect(() => {
    if (!mapId || !currentJobId) return
    if (completedJobIdRef.current === currentJobId) return

    let delay = 1000
    if (!pollStartTimestampRef.current) {
      pollStartTimestampRef.current = Date.now()
    }

    const poll = async () => {
      if (!isMountedRef.current) return

      // 300s (5-minute) timeout safety
      if (Date.now() - pollStartTimestampRef.current > 300000) {
        const timeoutMsg =
          'Generation is taking longer than usual. The worker is still processing in the background. You can check its progress on your dashboard.'
        if (isMountedRef.current) {
          setGenerationError(timeoutMsg)
        }
        if (onError) onError(timeoutMsg)
        return
      }

      try {
        const job = await mindMapService.pollJobStatus(mapId, currentJobId)
        if (!isMountedRef.current) return
        pollErrorCountRef.current = 0

        const percent = Number(job.progress_percent) || 0
        setProgress(Math.max(percent, 20))

        if (percent < 25) {
          setCurrentStep(1)
        } else if (percent < 55) {
          setCurrentStep(2)
        } else if (percent < 90) {
          setCurrentStep(3)
        } else {
          setCurrentStep(4)
        }

        // Terminal state: completed
        if (job.status === 'completed') {
          completedJobIdRef.current = currentJobId
          setProgress(100)
          setCurrentStep(4)

          // Fetch freshly generated graph and updated map details
          const [graphData, mapData] = await Promise.all([
            mindMapService.getMapGraph(mapId),
            mindMapService.getMapById(mapId).catch(() => null),
          ])

          if (!isMountedRef.current) return

          if (onComplete) {
            onComplete({
              mapId,
              graphData: graphData || { nodes: [], edges: [] },
              mapData,
              job,
            })
          }
          return
        }

        // Terminal state: failed or rejected
        if (job.status === 'failed' || job.status === 'rejected') {
          if (isRegenRef.current) {
            // CRITICAL (P0): NEVER delete the map or its graph on regeneration failure!
            const errMsg =
              job.error_message ||
              'Regeneration could not be completed. Your existing mind map has been preserved.'
            if (isMountedRef.current) {
              setGenerationError(errMsg)
            }
            if (onError) onError(errMsg)
            return
          }

          // Initial uncompleted empty draft cleanup only
          await mindMapService.deleteMap(mapId).catch(() => null)
          const errMsg =
            job.error_message ||
            'Generation could not be completed. The empty draft has been cleaned up.'
          if (isMountedRef.current) {
            setGenerationError(errMsg)
          }
          if (onError) onError(errMsg)
          return
        }

        // Schedule next poll with gentle backoff
        delay = Math.min(delay + 250, 2500)
        if (isMountedRef.current) {
          pollTimerRef.current = setTimeout(poll, delay)
        }
      } catch (pollErr) {
        if (!isMountedRef.current) return
        pollErrorCountRef.current += 1
        console.warn(`Polling notice (attempt ${pollErrorCountRef.current}):`, pollErr)

        if (pollErrorCountRef.current >= 6) {
          const lostMsg =
            'Lost connection to synthesis server. Please check your network or check your dashboard.'
          if (isMountedRef.current) {
            setGenerationError(lostMsg)
          }
          if (onError) onError(lostMsg)
          return
        }

        delay = Math.min(delay * 1.5, 3000)
        if (isMountedRef.current) {
          pollTimerRef.current = setTimeout(poll, delay)
        }
      }
    }

    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
    pollTimerRef.current = setTimeout(poll, delay)

    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [mapId, currentJobId, onComplete, onError])

  // Cancel generation handler
  const handleCancelGeneration = useCallback(async () => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }

    if (isRegenRef.current) {
      // Non-destructive stop: preserve existing map and return
      if (onCancel) {
        onCancel()
      } else {
        navigate(`/editor/${mapId}`, { replace: true, state: { mode: 'editor' } })
      }
      return
    }

    // Destructive cancel ONLY for empty initial drafts
    setIsCancelling(true)
    try {
      if (mapId) {
        await mindMapService.deleteMap(mapId)
      }
    } catch (err) {
      console.warn('Failed to delete cancelled map:', err)
    }

    if (onCancel) {
      onCancel()
    } else {
      navigate('/create')
    }
  }, [mapId, onCancel, navigate])

  // Leave generation handler: allows ARQ worker to continue in the background
  const handleLeaveGeneration = useCallback(() => {
    setIsLeaving(true)
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
    navigate('/dashboard')
  }, [navigate])

  const content = (
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
            {isRegenRef.current ? (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => {
                  setGenerationError('')
                  if (onCancel) {
                    onCancel()
                  } else {
                    navigate(`/editor/${mapId}`, { replace: true, state: { mode: 'editor' } })
                  }
                }}
                style={{ fontSize: '12px', padding: '6px 12px' }}
              >
                Return to Mind Map
              </button>
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
          {isRegenRef.current ? (
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
  )

  if (!fullPage) {
    return content
  }

  return (
    <div className="generating-layout">
      <AppHeader />
      <main className="generating-main">
        {content}
        <div className="diagnostics-panel">
          <div className="diagnostics-left">
            <span className="diagnostics-dot" />
            <span>ARQ Worker Engine / Graph Hierarchy Generator</span>
          </div>
          <div className="diagnostics-latency">LIVE PIPELINE</div>
        </div>
      </main>
      <AppFooter />
    </div>
  )
}

export default GenerationProgressView
