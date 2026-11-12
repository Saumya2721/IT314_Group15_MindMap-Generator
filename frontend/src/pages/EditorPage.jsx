import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { mindMapService } from '../services/mindMapService'
import { EditorWorkspace } from '../components/canvas/EditorWorkspace'
import { GenerationProgressView } from '../components/generator/GenerationProgressView'
import './GeneratingPage.css'

/**
 * EditorPage
 *
 * Dedicated page for /editor/:id route. Encapsulates graph fetching,
 * active job resumption, in-place map regeneration, and renders EditorWorkspace.
 */
export default function EditorPage() {
  const { id: mapId } = useParams()
  const navigate = useNavigate()

  const [mapTitle, setMapTitle] = useState('Mind Map')
  const [graphNodes, setGraphNodes] = useState([])
  const [rawEdges, setRawEdges] = useState([])
  const [isLoading, setIsLoading] = useState(true)

  // Regeneration & Job Polling State
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [activeJobId, setActiveJobId] = useState(null)
  const [toastMessage, setToastMessage] = useState('')

  const showToast = useCallback((msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }, [])

  // Load map metadata & graph
  useEffect(() => {
    if (!mapId) return
    setIsLoading(true)

    const fetchMapAndGraph = async () => {
      try {
        const mapData = await mindMapService.getMapById(mapId)
        if (mapData?.title) {
          setMapTitle(mapData.title)
        }

        // If an ARQ generation job is currently running, resume polling
        if (mapData?.latest_job_status === 'running' || mapData?.latest_job_status === 'pending') {
          setActiveJobId(mapData.latest_job_id)
          setIsRegenerating(true)
          setIsLoading(false)
          return
        }

        // Otherwise load existing graph
        const graphData = await mindMapService.getMapGraph(mapId)
        setGraphNodes(graphData?.nodes || [])
        setRawEdges(graphData?.edges || [])
      } catch (err) {
        console.error('Failed to load mind map workspace:', err)
        showToast(err.message || 'Failed to load mind map.')
      } finally {
        setIsLoading(false)
      }
    }

    fetchMapAndGraph()
  }, [mapId, showToast])

  // Trigger regeneration from within the editor
  const handleRegenerate = useCallback(
    async ({ instruction, detailLevel }) => {
      if (!mapId || !instruction) return

      try {
        const res = await mindMapService.regenerateMap(mapId, {
          instruction,
          detail_level: detailLevel,
        })
        setActiveJobId(res.job_id)
        setIsRegenerating(true)
      } catch (err) {
        showToast(err.message || 'Failed to trigger regeneration. Existing mind map preserved.')
      }
    },
    [mapId, showToast],
  )

  // Handle regeneration completion: automatically updates graph and displays editor
  const handleRegenerationComplete = useCallback(
    ({ graphData, mapData }) => {
      if (mapData?.title) {
        setMapTitle(mapData.title)
      }
      setGraphNodes(graphData?.nodes || [])
      setRawEdges(graphData?.edges || [])
      setIsRegenerating(false)
      setActiveJobId(null)
      showToast('Topological synthesis complete. Workspace ready.')
    },
    [showToast],
  )

  // Handle cancel during regeneration: preserves existing map (P0 invariant)
  const handleRegenerationCancel = useCallback(() => {
    setIsRegenerating(false)
    setActiveJobId(null)
    showToast('Regeneration stopped. Existing mind map preserved.')
  }, [showToast])

  // Loading skeleton
  if (isLoading) {
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

  // Active Regeneration / Job Polling View
  if (isRegenerating) {
    return (
      <GenerationProgressView
        mapId={mapId}
        jobId={activeJobId}
        mapTitle={mapTitle}
        isRegeneration={true}
        existingNodes={graphNodes}
        onComplete={handleRegenerationComplete}
        onCancel={handleRegenerationCancel}
        onError={(err) => showToast(err)}
        fullPage={true}
      />
    )
  }

  // Visual Editor Workspace
  return (
    <EditorWorkspace
      mapId={mapId}
      initialTitle={mapTitle}
      initialNodes={graphNodes}
      initialEdges={rawEdges}
      onTitleChange={setMapTitle}
      onBack={() => navigate('/dashboard')}
      onRegenerate={handleRegenerate}
      toastMessage={toastMessage}
      onClearToast={() => setToastMessage('')}
    />
  )
}
