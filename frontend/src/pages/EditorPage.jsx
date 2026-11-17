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
  const [initialViewport, setInitialViewport] = useState(null)
  const [mapVersion, setMapVersion] = useState(null)
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

        // Persisted editor viewport (S2-FE5 / US-08): restore the zoom level
        // and pan position the user left behind on the previous session.
        if (mapData?.view_x != null && mapData?.view_y != null && mapData?.view_zoom != null) {
          setInitialViewport({
            x: Number(mapData.view_x),
            y: Number(mapData.view_y),
            zoom: Number(mapData.view_zoom),
          })
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
        if (graphData?.version != null) {
          setMapVersion(graphData.version)
        } else if (mapData?.version != null) {
          setMapVersion(mapData.version)
        }
      } catch (err) {
        console.error('Failed to load mind map workspace:', err)
        showToast(err.message || 'Failed to load mind map.')
      } finally {
        setIsLoading(false)
      }
    }

    fetchMapAndGraph()
  }, [mapId, showToast])

  // Window focus revalidation: check if map version changed remotely (e.g. from background task or another tab)
  useEffect(() => {
    if (!mapId) return
    const handleFocus = async () => {
      if (isRegenerating || isLoading) return
      try {
        const latestMeta = await mindMapService.getMapById(mapId)
        if (latestMeta?.version && mapVersion && latestMeta.version > mapVersion) {
          const graphData = await mindMapService.getMapGraph(mapId)
          if (graphData?.nodes) {
            setGraphNodes(graphData.nodes)
            setRawEdges(graphData.edges || [])
            setMapVersion(graphData.version ?? latestMeta.version)
            showToast('Mind map updated with latest changes.')
          }
        }
      } catch (err) {
        // Silent catch for background focus check
      }
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [mapId, isRegenerating, isLoading, mapVersion, showToast])

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

  // Persist the latest canvas viewport to the server (S2-FE5 / US-08)
  const handleViewportChange = useCallback(
    async (viewport) => {
      if (!mapId || !viewport) return
      try {
        await mindMapService.updateMap(mapId, {
          view_x: viewport.x,
          view_y: viewport.y,
          view_zoom: viewport.zoom,
        })
      } catch (err) {
        console.warn('Failed to persist viewport:', err)
      }
    },
    [mapId],
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
      initialViewport={initialViewport}
      initialVersion={mapVersion}
      onTitleChange={setMapTitle}
      onBack={() => navigate('/dashboard')}
      onRegenerate={handleRegenerate}
      onViewportChange={handleViewportChange}
      toastMessage={toastMessage}
      onClearToast={() => setToastMessage('')}
    />
  )
}
