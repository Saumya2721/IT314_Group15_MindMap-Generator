import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate, useLocation, useParams } from 'react-router-dom'
import { useMindMap } from '../context/MindMapContext'
import { mindMapService } from '../services/mindMapService'
import { GenerationProgressView } from '../components/generator/GenerationProgressView'
import { EditorWorkspace } from '../components/canvas/EditorWorkspace'
import './GeneratingPage.css'

export default function GeneratingPage({ initialPhase }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { id: routeMapId } = useParams()
  const { draftMap } = useMindMap()

  // Determine initial entry phase
  const isDirectEditor =
    initialPhase === 'editor' || location.state?.mode === 'editor' || Boolean(routeMapId)

  // Active Map, Job, and Phase Tracking
  const [mapId, setMapId] = useState(routeMapId || location.state?.mapId || null)
  const [jobId, setJobId] = useState(location.state?.jobId || null)
  const [isLoadingMap, setIsLoadingMap] = useState(Boolean(routeMapId))
  const [isRegeneration, setIsRegeneration] = useState(Boolean(location.state?.isRegeneration))
  const [phase, setPhase] = useState(isDirectEditor ? 'editor' : 'generating')

  // Map Title, Topic, Thoughts
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
  const files = useMemo(() => location.state?.files || [], [location.state?.files])

  // Graph state (nodes & edges directly from backend)
  const [graphNodes, setGraphNodes] = useState([])
  const [rawEdges, setRawEdges] = useState([])

  // Toast feedback
  const [toastMessage, setToastMessage] = useState('')

  const showToast = useCallback((msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }, [])

  // Load existing map if opened via /editor/:id
  useEffect(() => {
    if (!routeMapId) return

    setMapId(routeMapId)
    setIsLoadingMap(true)

    const loadMapData = async () => {
      try {
        const mapData = await mindMapService.getMapById(routeMapId)
        if (mapData?.title) {
          setMapTitle(mapData.title)
        }

        // If map is currently being synthesized, switch to generating/progress view
        if (mapData?.latest_job_status === 'running' || mapData?.latest_job_status === 'pending') {
          setJobId(mapData.latest_job_id)
          setPhase('generating')
          setIsLoadingMap(false)
          return
        }

        // Otherwise load graph nodes and edges
        const graphData = await mindMapService.getMapGraph(routeMapId)
        setGraphNodes(graphData?.nodes || [])
        setRawEdges(graphData?.edges || [])
        setPhase('editor')
      } catch (err) {
        console.error('Failed to load map graph:', err)
        showToast(`Error loading map: ${err.message}`)
      } finally {
        setIsLoadingMap(false)
      }
    }

    loadMapData()
  }, [routeMapId, showToast])

  // Handle generation completion from GenerationProgressView
  const handleGenerationComplete = useCallback(
    ({ mapId: completedMapId, graphData, mapData }) => {
      if (mapData?.title) {
        setMapTitle(mapData.title)
      }
      setGraphNodes(graphData?.nodes || [])
      setRawEdges(graphData?.edges || [])
      setPhase('editor')
      setIsLoadingMap(false)
      showToast('Topological synthesis complete. Workspace ready.')

      const targetId = completedMapId || mapId
      if (targetId && !routeMapId) {
        navigate(`/editor/${targetId}`, { replace: true, state: { mode: 'editor' } })
      }
    },
    [mapId, routeMapId, navigate, showToast],
  )

  // Handle cancel during generation
  const handleGenerationCancel = useCallback(() => {
    const isRegenMode = isRegeneration || graphNodes.length > 0
    if (isRegenMode) {
      setPhase('editor')
      showToast('Regeneration stopped. Existing mind map preserved.')
      const activeId = mapId || routeMapId
      if (activeId) {
        navigate(`/editor/${activeId}`, { replace: true, state: { mode: 'editor' } })
      }
      return
    }

    navigate('/create')
  }, [isRegeneration, graphNodes.length, mapId, routeMapId, navigate, showToast])

  // Trigger regeneration from editor workspace
  const handleRegenerate = useCallback(
    async ({ instruction, detailLevel: regenDetailLevel }) => {
      const activeMapId = mapId || routeMapId
      if (!activeMapId || !instruction) return

      setIsRegeneration(true)
      setPhase('generating')

      try {
        const res = await mindMapService.regenerateMap(activeMapId, {
          instruction,
          detail_level: regenDetailLevel,
        })
        setJobId(res.job_id)
      } catch (err) {
        showToast(err.message || 'Failed to trigger regeneration. Mind map preserved.')
        setPhase('editor')
      }
    },
    [mapId, routeMapId, showToast],
  )

  // Loading skeleton
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

  // Phase: Editor Workspace
  if (phase === 'editor') {
    return (
      <EditorWorkspace
        mapId={mapId || routeMapId}
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

  // Phase: Generation Progress View
  return (
    <GenerationProgressView
      mapId={mapId || routeMapId}
      jobId={jobId}
      initialParams={
        jobId
          ? null
          : {
              topic,
              ideas: thoughts,
              detail_level: detailLevel,
              files,
            }
      }
      mapTitle={mapTitle}
      isRegeneration={isRegeneration}
      existingNodes={graphNodes}
      onComplete={handleGenerationComplete}
      onCancel={handleGenerationCancel}
      onError={(err) => showToast(err)}
      fullPage={true}
    />
  )
}
