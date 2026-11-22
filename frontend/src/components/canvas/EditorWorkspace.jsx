import React, { useState, useEffect, useRef, useCallback } from 'react'
import { CanvasTopBar } from './CanvasTopBar'
import { MindMapCanvas } from './MindMapCanvas'
import { NodeInspector } from './NodeInspector'
import AddNodeDialog from './AddNodeDialog'
import EdgeInspector from './EdgeInspector'
import OutlineView from './OutlineView'
import { VersionHistoryPanel } from '../history/VersionHistoryPanel'
import { ChatPanel } from './ChatPanel'
import { MapSourcesSidebar } from './MapSourcesSidebar'
import { ResearchDegradedBanner } from './ResearchDegradedBanner'
import { ExportModal } from './ExportModal'
import { exportMindMap } from '../../services/exportService'
import { mindMapService } from '../../services/mindMapService'
import { getDescendantNodeIds, validateConnection } from '../../utils/connectionGuards'
import { useCanvasState, generateUUID } from './useCanvasState'
import { useCommentsChat } from './useCommentsChat'
import { useExportHistory } from './useExportHistory'
import { useMindMap } from '../../context/MindMapContext'
import { useSaveStatus } from './useSaveStatus'
import '../../pages/GeneratingPage.css'

/**
 * Helper to determine if an event target is an active editable input.
 */
export function isEditableElement(target) {
  if (!target) return false
  const tagName = target?.tagName ? target.tagName.toUpperCase() : ''
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName)) return true
  if (target?.isContentEditable || target?.getAttribute?.('contenteditable') === 'true') return true
  return false
}

/**
 * Handles global canvas keyboard shortcuts for Undo (Ctrl+Z) and Redo (Ctrl+Y, Ctrl+Shift+Z).
 * Returns true if the shortcut was handled, false otherwise.
 */
export function handleCanvasKeyboardShortcut({
  event,
  canUndo = false,
  canRedo = false,
  onUndo,
  onRedo,
  isModalActive = false,
}) {
  if (!event) return false

  // 1. Do not trigger while user is typing in input, textarea, or contentEditable element
  if (isEditableElement(event.target)) return false

  // 2. Do not trigger if any modal is active
  if (isModalActive) return false

  // 3. Must be Ctrl (or Cmd on macOS) without Alt
  const isCtrlOrMeta = Boolean(event.ctrlKey || event.metaKey)
  if (!isCtrlOrMeta || event.altKey) return false

  const key = event.key

  if (key === 'z' || key === 'Z') {
    event.preventDefault?.()
    if (event.shiftKey) {
      if (canRedo && onRedo) onRedo()
    } else {
      if (canUndo && onUndo) onUndo()
    }
    return true
  }

  if (key === 'y' || key === 'Y') {
    event.preventDefault?.()
    if (canRedo && onRedo) onRedo()
    return true
  }

  return false
}

/**
 * EditorWorkspace
 *
 * Dedicated workspace encapsulating the React Flow canvas, top bar,
 * inspectors, outline view, and modal dialogs. Extracted from GeneratingPage.jsx
 * as part of Issue #104.
 *
 * Uses isolated state hooks for:
 * - FE-1: Canvas and undo/redo (useCanvasState)
 * - FE-2: Comments and chat (useCommentsChat)
 * - FE-3: Export and history (useExportHistory)
 */
export function EditorWorkspace({
  mapId,
  initialTitle = 'Mind Map',
  initialNodes = [],
  initialEdges = [],
  initialViewport = null,
  initialVersion = null,
  initialMetadata = null,
  onTitleChange = null,
  onTitleBlur = null,
  onBack = null,
  onRegenerate = null,
  onViewportChange = null,
  toastMessage = '',
  onClearToast = null,
  canvasState: customCanvasState = null,
  commentsChatState: customCommentsChatState = null,
  exportHistoryState: customExportHistoryState = null,
}) {
  const { updateMap } = useMindMap()

  // Map Title & Saving State
  const [mapTitle, setMapTitle] = useState(initialTitle)

  // Local Toast State
  const [localToast, setLocalToast] = useState('')

  // Regeneration Modal State
  const [showRegenModal, setShowRegenModal] = useState(false)
  const [regenInstruction, setRegenInstruction] = useState('')
  const [regenDetailLevel, setRegenDetailLevel] = useState('overview')

  // Export Modal State (S2-FE6 / US-07)
  const [showExportModal, setShowExportModal] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [exportProgress, setExportProgress] = useState(0)
  const [exportError, setExportError] = useState('')
  const flowApiRef = useRef(null)

  // Branch Deletion Confirmation State
  const [deleteConfirmNode, setDeleteConfirmNode] = useState(null)
  const hasUserEditedRef = useRef(false)

  // S3-FE3: Research Sources Sidebar & Research Degradation Notice
  const [showSourcesSidebar, setShowSourcesSidebar] = useState(false)
  const [researchNoticeDismissed, setResearchNoticeDismissed] = useState(false)
  const isResearchDegraded =
    !researchNoticeDismissed &&
    Boolean(
      initialMetadata?.web_research_degraded ||
        initialMetadata?.research_degraded ||
        initialMetadata?.degraded_reason,
    )

  const displayToast = useCallback((msg) => {
    setLocalToast(msg)
    setTimeout(() => setLocalToast(''), 3500)
  }, [])

  // FE-1: Canvas & Undo/Redo State
  const defaultCanvas = useCanvasState({
    initialNodes,
    initialEdges,
  })
  const canvas = customCanvasState || defaultCanvas
  const { setGraphData } = canvas

  // S2-FE5 / US-08: unified autosave persistence callback (title and graph mutations with OCC)
  const mapVersionRef = useRef(initialVersion ?? null)
  const versionPromiseRef = useRef(null)

  useEffect(() => {
    if (initialVersion !== null && initialVersion !== undefined) {
      mapVersionRef.current = initialVersion
    }
  }, [initialVersion])

  useEffect(() => {
    if (!mapId) return
    if (initialVersion !== null && initialVersion !== undefined) {
      mapVersionRef.current = initialVersion
      return
    }
    versionPromiseRef.current = mindMapService
      .getMapGraph(mapId)
      .then((data) => {
        if (data?.version !== undefined) {
          mapVersionRef.current = data.version
        }
        return data?.version
      })
      .catch((err) => {
        console.error('Failed to load initial graph version:', err)
        return null
      })
  }, [mapId, initialVersion])

  const persistCurrentState = useCallback(async () => {
    if (!mapId) return
    const promises = []
    if (mapTitle && mapTitle.trim()) {
      promises.push(updateMap(mapId, { title: mapTitle.trim() }))
    }
    if (hasUserEditedRef.current && canvas.nodes.length > 0) {
      if (mapVersionRef.current === null && versionPromiseRef.current) {
        await versionPromiseRef.current
      }
      if (mapVersionRef.current === null) {
        setLocalToast(
          'Unable to determine graph version. Save paused to prevent overwriting changes.',
        )
        throw new Error('Graph version unavailable for optimistic lock')
      }
      promises.push(
        mindMapService
          .updateMapGraph(mapId, {
            nodes: canvas.nodes,
            edges: canvas.edges,
            expected_version: mapVersionRef.current,
          })
          .then((res) => {
            if (res?.version !== undefined) {
              mapVersionRef.current = res.version
            }
          })
          .catch((err) => {
            const isConflict =
              err?.status === 409 ||
              err?.statusCode === 409 ||
              err?.message?.includes('409') ||
              err?.message?.includes('Conflict') ||
              err?.detail?.error === 'OptimisticLockConflict' ||
              err?.error === 'OptimisticLockConflict'
            if (isConflict) {
              setLocalToast('Conflict: Mind map was modified concurrently. Please reload to sync.')
            }
            throw err
          }),
      )
    }
    await Promise.all(promises)
  }, [mapId, mapTitle, updateMap, canvas.nodes, canvas.edges])

  // S2-FE5 / US-08: autosave status machine for the editor header
  const autosave = useSaveStatus(persistCurrentState, { debounceMs: 400 })
  const { scheduleSave } = autosave
  const saveStatus = autosave.status

  // S2-FE5 / US-08: autosave title changes (debounced 400ms)
  useEffect(() => {
    if (!mapId || !mapTitle.trim()) return
    scheduleSave()
  }, [mapTitle, mapId, scheduleSave])

  // S2-FE5 / US-08: autosave node positions/viewport on graph change
  useEffect(() => {
    if (!mapId || canvas.nodes.length === 0 || !hasUserEditedRef.current) return
    scheduleSave()
  }, [canvas.nodes, canvas.edges, mapId, scheduleSave])

  // S2-FE5 / US-08: autosave viewport changes (debounced 500ms inside hook)
  const handleViewportChange = useCallback(
    (viewport) => {
      if (!onViewportChange || !viewport) return
      onViewportChange(viewport)
    },
    [onViewportChange],
  )

  // Synchronize canvas if initialNodes or initialEdges update externally
  useEffect(() => {
    if (initialNodes && initialNodes.length > 0) {
      setGraphData({ nodes: initialNodes, edges: initialEdges || [] })
      hasUserEditedRef.current = false
    }
  }, [initialNodes, initialEdges, setGraphData])

  // Sync initialTitle
  useEffect(() => {
    if (initialTitle) {
      setMapTitle(initialTitle)
    }
  }, [initialTitle])

  // FE-2: Comments & Chat State
  const defaultCommentsChat = useCommentsChat({
    mapId,
    activeNodeId: canvas.selectedNode?.id,
  })
  const commentsChat = customCommentsChatState || defaultCommentsChat

  // S2-FE4: Load chat history when map is ready
  useEffect(() => {
    if (mapId) commentsChat.loadChatHistory(mapId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapId])

  // S2-FE4: Handle chat action proposal confirmation
  const handleConfirmChatAction = useCallback(
    async (messageId, proposedAction) => {
      const res = await commentsChat.confirmAction(messageId, proposedAction)
      if (res && mapId) {
        const jobId = res?.data?.job_id || res?.job_id
        const progressMsgId = jobId ? `progress-${jobId}` : null
        try {
          if (jobId) {
            displayToast('Action confirmed and queued. Updating graph...')
            commentsChat.dispatch?.({
              type: 'ADD_MESSAGE',
              payload: {
                id: progressMsgId,
                role: 'assistant',
                content: '⏳ Starting branch regeneration… (0%)',
                is_confirmed: null,
                metadata: {
                  execution_status: 'in_progress',
                  progress_percent: 0,
                  stage: 'Starting',
                },
                created_at: new Date().toISOString(),
              },
            })

            let attempts = 0
            while (attempts < 60) {
              await new Promise((r) => setTimeout(r, 1000))
              attempts++
              const jobStatus = await mindMapService.pollJobStatus(mapId, jobId)
              const pct = jobStatus?.percent_complete ?? Math.min(attempts * 4, 95)
              const stage = jobStatus?.status || 'processing'

              commentsChat.dispatch?.({
                type: 'UPDATE_MESSAGE',
                payload: {
                  id: progressMsgId,
                  content: `⏳ Regenerating branch… (${pct}%) [${stage}]`,
                  metadata: { execution_status: 'in_progress', progress_percent: pct, stage },
                },
              })

              if (jobStatus?.status === 'completed') {
                break
              }
              if (jobStatus?.status === 'failed' || jobStatus?.status === 'rejected') {
                throw new Error(jobStatus.error_message || 'Regeneration task failed.')
              }
            }

            commentsChat.dispatch?.({
              type: 'UPDATE_MESSAGE',
              payload: {
                id: progressMsgId,
                content: '✅ Branch regeneration complete! Graph updated on canvas.',
                metadata: {
                  execution_status: 'applied',
                  progress_percent: 100,
                  stage: 'Completed',
                },
              },
            })
          }
          const updated = await mindMapService.getMapGraph(mapId)
          if (updated && updated.nodes) {
            if (updated.version !== undefined) {
              mapVersionRef.current = updated.version
            }
            canvas.setGraphData({
              nodes: updated.nodes,
              edges: updated.edges || [],
            })
            displayToast('Proposed action successfully applied to graph.')
          }
        } catch (err) {
          console.warn('Could not refresh graph after action confirmation:', err)
          if (progressMsgId) {
            commentsChat.dispatch?.({
              type: 'UPDATE_MESSAGE',
              payload: {
                id: progressMsgId,
                content: `❌ Regeneration failed: ${err.message}`,
                metadata: { execution_status: 'failed' },
              },
            })
          }
          displayToast(err.message || 'Action confirmed, but graph refresh failed.', 'error')
        }
      }
    },
    [commentsChat, mapId, canvas, displayToast],
  )

  // S2-FE4: Handle chat action proposal dismissal
  const handleDismissChatAction = useCallback(
    (messageId) => {
      commentsChat.dismissAction(messageId)
      displayToast('Proposal dismissed.')
    },
    [commentsChat, displayToast],
  )

  // FE-3: Export & History State
  const defaultExportHistory = useExportHistory({
    mapId,
    mapTitle,
    onRestoreGraph: (restoredData) => {
      canvas.setGraphData({
        nodes: restoredData.nodes || [],
        edges: restoredData.edges || [],
      })
      if (restoredData.version !== undefined) {
        mapVersionRef.current = restoredData.version
      } else if (restoredData.new_version !== undefined) {
        mapVersionRef.current = restoredData.new_version
      }
      if (mapId) {
        mindMapService
          .getMapById(mapId)
          .then((meta) => {
            if (meta?.version != null) {
              mapVersionRef.current = meta.version
            }
          })
          .catch(() => {})
      }
      displayToast('Map restored from snapshot.')
    },
  })
  const exportHistory = customExportHistoryState || defaultExportHistory

  // S2-FE3: Branch AI Regeneration state
  const [isBranchRegenerating, setIsBranchRegenerating] = useState(false)
  const [branchRegenProgress, setBranchRegenProgress] = useState(0)
  const [branchRegenStatus, setBranchRegenStatus] = useState('')
  const lastBranchRegenRef = useRef(null)

  const handleRegenerateBranch = async (nodeId, instruction = '') => {
    const targetNode = canvas.nodes.find((n) => n.id === nodeId)
    if (!targetNode) return

    // Save snapshot of current graph so failure leaves canvas state intact
    const snapshotNodes = [...canvas.nodes]
    const snapshotEdges = [...canvas.edges]
    lastBranchRegenRef.current = { nodeId, instruction }

    setIsBranchRegenerating(true)
    setBranchRegenProgress(15)
    setBranchRegenStatus('Initiating branch regeneration...')

    try {
      if (mapId) {
        // Real backend branch regeneration
        const jobRes = await mindMapService.regenerateBranch(mapId, nodeId, { instruction })
        const jobId = jobRes?.job_id

        if (jobId) {
          let attempts = 0
          const maxAttempts = 60
          let completed = false

          while (attempts < maxAttempts && !completed) {
            await new Promise((r) => setTimeout(r, 1000))
            attempts++
            const jobStatus = await mindMapService.pollJobStatus(mapId, jobId)

            if (jobStatus) {
              setBranchRegenProgress(Math.min(95, 20 + attempts * 5))
              if (jobStatus.status === 'completed') {
                completed = true
                setBranchRegenProgress(100)
                setBranchRegenStatus('Updating canvas nodes...')
                const updated = await mindMapService.getMapGraph(mapId)
                if (updated && updated.nodes) {
                  if (updated.version !== undefined) {
                    mapVersionRef.current = updated.version
                  }
                  canvas.setGraphData({
                    nodes: updated.nodes,
                    edges: updated.edges || [],
                  })
                }
                const targetLabel = targetNode.label || targetNode.title || 'Concept'
                displayToast(`Branch "${targetLabel}" successfully regenerated.`)
                return
              } else if (jobStatus.status === 'failed' || jobStatus.status === 'rejected') {
                throw new Error(jobStatus.error_message || 'Branch regeneration failed.')
              } else {
                setBranchRegenStatus(
                  `Generating branch concepts (${jobStatus.progress_percent || attempts * 5}%)...`,
                )
              }
            }
          }

          if (!completed) {
            throw new Error('Branch regeneration timed out. Please try again.')
          }
        } else {
          throw new Error('Regeneration job could not be created.')
        }
      } else {
        throw new Error('Map identifier missing for branch regeneration.')
      }
    } catch (err) {
      // Leave canvas state completely intact on failure
      canvas.setGraphData({ nodes: snapshotNodes, edges: snapshotEdges })
      displayToast(`Branch regeneration failed: ${err.message || err}. Canvas state preserved.`)
    } finally {
      setIsBranchRegenerating(false)
      setBranchRegenProgress(0)
      setBranchRegenStatus('')
    }
  }

  // Title change handlers
  const handleTitleChangeInternal = (newTitle) => {
    setMapTitle(newTitle)
    if (onTitleChange) onTitleChange(newTitle)
  }

  const handleTitleBlurInternal = async () => {
    if (onTitleBlur) {
      onTitleBlur()
      return
    }
    if (!mapId || !mapTitle.trim()) return
    await autosave.flushSave()
  }

  // Share handler
  const handleShare = () => {
    navigator.clipboard?.writeText(window.location.href)
    displayToast('Shareable workspace link copied to clipboard.')
  }

  // Export handler: opens the PDF / PNG export dialog (S2-FE6 / US-07)
  const handleExport = () => {
    setExportError('')
    setExportProgress(0)
    setShowExportModal(true)
    exportHistory.openExport()
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
      exportHistory.closeExport()
      displayToast(`${result.format.toUpperCase()} exported successfully.`)
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
    exportHistory.closeExport()
  }

  // Regeneration submission handler
  const handleConfirmRegenerate = (e) => {
    e.preventDefault()
    if (!regenInstruction.trim()) return

    setShowRegenModal(false)
    const instruction = regenInstruction.trim()
    const detail = regenDetailLevel

    if (onRegenerate) {
      onRegenerate({ instruction, detailLevel: detail })
    }
  }

  // Node position drag handler
  const handleNodePositionChange = (id, x, y) => {
    hasUserEditedRef.current = true
    canvas.updateNodePosition(id, x, y)
  }

  // Connection handler with validation & toast warning feedback
  const handleConnect = (connection) => {
    const validation = validateConnection(canvas.nodes, canvas.edges, connection)
    if (!validation.valid) {
      displayToast(validation.reason)
      return
    }
    hasUserEditedRef.current = true
    canvas.addEdge(connection)
    displayToast('Concepts connected.')
  }

  // Edge reconnection handler with validation & toast feedback
  const handleReconnect = (oldEdge, newConnection) => {
    const filteredEdges = canvas.edges.filter((e) => String(e.id) !== String(oldEdge.id || oldEdge))
    const validation = validateConnection(canvas.nodes, filteredEdges, newConnection)
    if (!validation.valid) {
      displayToast(validation.reason)
      return
    }
    hasUserEditedRef.current = true
    canvas.reconnectEdge(oldEdge, newConnection)
    displayToast('Connection updated.')
  }

  // Inline editing handlers
  const handleStartEditing = (nodeId) => {
    canvas.startEditing(nodeId)
  }

  const handleSaveLabel = (id, label) => {
    hasUserEditedRef.current = true
    canvas.updateNodeLabel(id, label)
  }

  const handleCancelEditing = () => {
    canvas.stopEditing()
  }

  const handleUpdateDescription = (id, desc) => {
    hasUserEditedRef.current = true
    canvas.updateNode(id, { description: desc })
  }

  // Node deletion with branch confirmation check
  const handleDeleteNode = (nodeId) => {
    const targetNode = canvas.nodes.find((n) => String(n.id) === String(nodeId))
    if (!targetNode) return

    if (targetNode.node_type === 'root') {
      displayToast('The central root concept cannot be deleted.')
      return
    }

    const descendantIds = getDescendantNodeIds(canvas.edges, nodeId)
    if (descendantIds.length > 0) {
      setDeleteConfirmNode({
        id: nodeId,
        label: targetNode.label || 'Concept',
        descendantCount: descendantIds.length,
      })
    } else {
      hasUserEditedRef.current = true
      canvas.deleteNode(nodeId)
      displayToast(`Concept "${targetNode.label || 'Concept'}" deleted.`)
    }
  }

  // Confirm branch deletion
  const handleConfirmDeleteBranch = () => {
    if (!deleteConfirmNode) return
    hasUserEditedRef.current = true
    canvas.deleteBranch(deleteConfirmNode.id)
    displayToast(`Branch "${deleteConfirmNode.label}" and sub-concepts deleted.`)
    setDeleteConfirmNode(null)
  }

  // Add child concept
  const handleAddChild = (parentId) => {
    hasUserEditedRef.current = true
    canvas.addChildNode(parentId)
  }

  // Add sibling concept
  const handleAddSibling = (refId) => {
    const refNode = canvas.nodes.find((n) => String(n.id) === String(refId))
    if (refNode?.node_type === 'root') {
      displayToast('The central root concept cannot have siblings. Add a child concept instead.')
      return
    }
    hasUserEditedRef.current = true
    canvas.addSiblingNode(refId)
  }

  const handleUndo = useCallback(() => {
    if (!canvas.canUndo) return
    hasUserEditedRef.current = true
    canvas.undo()
  }, [canvas])

  const handleRedo = useCallback(() => {
    if (!canvas.canRedo) return
    hasUserEditedRef.current = true
    canvas.redo()
  }, [canvas])

  // Global Keyboard Shortcuts for Canvas: Ctrl+Z (Undo) and Ctrl+Y / Ctrl+Shift+Z (Redo)
  useEffect(() => {
    const handleGlobalKeyDown = (e) => {
      handleCanvasKeyboardShortcut({
        event: e,
        canUndo: canvas.canUndo,
        canRedo: canvas.canRedo,
        onUndo: handleUndo,
        onRedo: handleRedo,
        isModalActive: Boolean(
          showRegenModal || showExportModal || exportHistory.isExportModalOpen || deleteConfirmNode,
        ),
      })
    }

    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => {
      window.removeEventListener('keydown', handleGlobalKeyDown)
    }
  }, [
    handleUndo,
    handleRedo,
    canvas.canUndo,
    canvas.canRedo,
    showRegenModal,
    showExportModal,
    exportHistory.isExportModalOpen,
    deleteConfirmNode,
  ])

  // S2-FE3 / US-Shortcomings: Dialogs & Edge inspection state
  const [showAddNodeDialog, setShowAddNodeDialog] = useState(false)
  const [addNodeInitialParentId, setAddNodeInitialParentId] = useState(null)
  const [selectedEdge, setSelectedEdge] = useState(null)
  const [connectSourceNodeId, setConnectSourceNodeId] = useState(null)

  const handleOpenAddNode = useCallback(
    (parentId = null) => {
      setAddNodeInitialParentId(parentId || canvas.selectedNode?.id || null)
      setShowAddNodeDialog(true)
    },
    [canvas.selectedNode],
  )

  const handleAddNodeConfirm = useCallback(
    ({ label, nodeType, description, connectToNodeId, edgeLabel }) => {
      setShowAddNodeDialog(false)
      if (nodeType === 'root' && canvas.nodes.some((n) => n.node_type === 'root')) {
        displayToast('A mind map can only have one central root concept.', 'error')
        return
      }
      hasUserEditedRef.current = true
      const newNodeId = generateUUID()
      const isRoot = nodeType === 'root'
      const tint = isRoot ? '#2D5A47' : nodeType === 'branch' ? '#C96B4B' : '#899B72'
      const category = isRoot ? 'Root' : nodeType === 'branch' ? 'Branch' : 'Topic'

      let posX = 100
      let posY = 100
      if (connectToNodeId) {
        const parentNode = canvas.nodes.find((n) => n.id === connectToNodeId)
        if (parentNode) {
          posX = (parentNode.pos_x ?? parentNode.x ?? 100) + 180
          posY = (parentNode.pos_y ?? parentNode.y ?? 100) + 60
        }
      } else if (canvas.nodes.length > 0) {
        const last = canvas.nodes[canvas.nodes.length - 1]
        posX = (last.pos_x ?? last.x ?? 100) + 50
        posY = (last.pos_y ?? last.y ?? 100) + 80
      }

      const newNode = {
        id: newNodeId,
        label,
        description: description || '',
        node_type: nodeType,
        background_color: tint,
        category,
        pos_x: posX,
        pos_y: posY,
      }

      canvas.addNode(newNode)

      if (mapId) {
        mindMapService
          .createNode(mapId, {
            label,
            node_type: nodeType,
            description,
            pos_x: posX,
            pos_y: posY,
            background_color: tint,
            parent_node_id: connectToNodeId || undefined,
            edge_label: edgeLabel || undefined,
          })
          .catch((err) => {
            console.warn('Failed to immediately persist created node via API:', err)
          })
      }

      if (connectToNodeId) {
        const edgeId = generateUUID()
        canvas.addEdge({
          id: edgeId,
          source_node_id: connectToNodeId,
          target_node_id: newNodeId,
          label: edgeLabel || '',
        })
        if (mapId) {
          mindMapService
            .createEdge(mapId, {
              source_node_id: connectToNodeId,
              target_node_id: newNodeId,
              label: edgeLabel || '',
            })
            .catch((err) => {
              console.warn('Failed to persist created edge, rolling back canvas:', err)
              canvas.deleteEdge?.(edgeId)
              displayToast(`Failed to connect edge: ${err.message}`, 'error')
            })
        }
      }

      displayToast(`Added ${nodeType} node: "${label}"`)
    },
    [canvas, mapId, displayToast],
  )

  const handleEdgeClick = useCallback(
    (event, edge) => {
      canvas.clearSelectedNode()
      const fullEdge = canvas.edges.find((e) => String(e.id) === String(edge?.id)) || edge
      setSelectedEdge(fullEdge)
    },
    [canvas],
  )

  const handleSaveEdge = useCallback(
    async (edgeId, updates) => {
      hasUserEditedRef.current = true
      canvas.updateEdge?.(edgeId, updates)
      setSelectedEdge((prev) => (prev ? { ...prev, ...updates } : null))
      if (mapId) {
        try {
          await mindMapService.updateEdge(mapId, edgeId, updates)
          displayToast('Edge updated successfully.')
        } catch (err) {
          console.warn('Failed to save edge:', err)
          displayToast(`Failed to update edge: ${err.message}`, 'error')
        }
      }
    },
    [canvas, mapId, displayToast],
  )

  const handleDeleteEdge = useCallback(
    async (edgeId) => {
      hasUserEditedRef.current = true
      canvas.deleteEdge?.(edgeId)
      setSelectedEdge(null)
      if (mapId) {
        try {
          await mindMapService.deleteEdge(mapId, edgeId)
          displayToast('Edge deleted.')
        } catch (err) {
          console.warn('Failed to delete edge:', err)
          displayToast(`Failed to delete edge: ${err.message}`, 'error')
        }
      }
    },
    [canvas, mapId, displayToast],
  )

  const handleStartAddEdge = useCallback(
    (sourceNodeId) => {
      setConnectSourceNodeId(sourceNodeId)
      const sourceNode = canvas.nodes.find((n) => n.id === sourceNodeId)
      displayToast(`Click target concept to connect from "${sourceNode?.label || 'Node'}"…`)
    },
    [canvas.nodes, displayToast],
  )

  const handleNodeSelect = useCallback(
    (nodeOrId) => {
      setSelectedEdge(null)
      const targetId = typeof nodeOrId === 'string' ? nodeOrId : nodeOrId?.id
      if (connectSourceNodeId && targetId && connectSourceNodeId !== targetId) {
        hasUserEditedRef.current = true
        const edgeId = generateUUID()
        canvas.addEdge({
          id: edgeId,
          source_node_id: connectSourceNodeId,
          target_node_id: targetId,
          label: '',
        })
        if (mapId) {
          mindMapService
            .createEdge(mapId, {
              source_node_id: connectSourceNodeId,
              target_node_id: targetId,
              label: '',
            })
            .catch((err) => {
              console.warn('Failed to persist created edge, rolling back canvas:', err)
              canvas.deleteEdge?.(edgeId)
              displayToast(`Failed to connect edge: ${err.message}`, 'error')
            })
        }
        displayToast('Edge connected successfully.')
        setConnectSourceNodeId(null)
        return
      }
      setConnectSourceNodeId(null)
      canvas.selectNode(nodeOrId)
    },
    [connectSourceNodeId, canvas, mapId, displayToast],
  )

  const handlePaneClick = useCallback(() => {
    setSelectedEdge(null)
    setConnectSourceNodeId(null)
    canvas.clearSelectedNode()
  }, [canvas])

  const activeToast = toastMessage || localToast

  return (
    <div className="generating-editor-view">
      {/* Top Editor Navigation Bar */}
      <CanvasTopBar
        title={mapTitle}
        onTitleChange={handleTitleChangeInternal}
        onTitleBlur={handleTitleBlurInternal}
        onBack={onBack}
        onShare={handleShare}
        onExport={handleExport}
        onRegenerate={onRegenerate ? () => setShowRegenModal(true) : undefined}
        onHistory={exportHistory.openHistory}
        onToggleChat={commentsChat.toggleChat}
        onToggleSources={() => setShowSourcesSidebar((prev) => !prev)}
        canUndo={canvas.canUndo}
        canRedo={canvas.canRedo}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onAddNode={() => handleOpenAddNode()}
        viewMode={canvas.viewMode}
        onViewModeChange={canvas.setViewMode}
        saveStatus={saveStatus}
        onSaveRetry={() => {
          void autosave.retry()
        }}
      />

      {/* Visual Canvas or Outline Mode */}
      {canvas.viewMode === 'outline' ? (
        <OutlineView
          nodes={canvas.nodes}
          edges={canvas.edges}
          mapTitle={mapTitle}
          onSwitchToCanvas={() => canvas.setViewMode('canvas')}
          onSelectNode={(node) => {
            canvas.selectNode?.(node)
            canvas.setViewMode('canvas')
            if (
              flowApiRef.current?.setCenter &&
              (node.pos_x !== undefined || node.x !== undefined)
            ) {
              const nx = Number(node.pos_x ?? node.x ?? 0)
              const ny = Number(node.pos_y ?? node.y ?? 0)
              if (nx !== 0 || ny !== 0) {
                flowApiRef.current.setCenter(nx, ny, { duration: 500 })
              }
            }
          }}
        />
      ) : (
        <MindMapCanvas
          rawNodes={canvas.nodes}
          rawEdges={canvas.edges}
          toastMessage={activeToast}
          onClearToast={onClearToast || (() => setLocalToast(''))}
          onToast={displayToast}
          flowApiRef={flowApiRef}
          initialViewport={initialViewport}
          onViewportChange={handleViewportChange}
          selectedNode={canvas.selectedNode}
          editingNodeId={canvas.editingNodeId}
          onNodeSelect={handleNodeSelect}
          onPaneClick={handlePaneClick}
          onNodePositionChange={handleNodePositionChange}
          onConnect={handleConnect}
          onReconnect={handleReconnect}
          onStartEditing={handleStartEditing}
          onSaveLabel={handleSaveLabel}
          onCancelEditing={handleCancelEditing}
          onDeleteNode={handleDeleteNode}
          onAddChild={handleAddChild}
          onAddSibling={handleAddSibling}
          onEdgeClick={handleEdgeClick}
        />
      )}

      {/* Node Inspector Flyout */}
      {canvas.selectedNode && (
        <NodeInspector
          selectedNode={canvas.selectedNode}
          onClose={canvas.clearSelectedNode}
          onUpdateLabel={handleSaveLabel}
          onUpdateDescription={handleUpdateDescription}
          onAddChild={handleAddChild}
          onAddSibling={handleAddSibling}
          onDeleteNode={handleDeleteNode}
          onStartAddEdge={handleStartAddEdge}
          comments={commentsChat.commentsByNodeId[canvas.selectedNode?.id] || []}
          onAddComment={commentsChat.addComment}
          onResolveComment={commentsChat.resolveComment}
          onRegenerateBranch={handleRegenerateBranch}
          isRegenerating={isBranchRegenerating}
          regenerationProgress={branchRegenProgress}
          regenerationStatus={branchRegenStatus}
        />
      )}

      {/* Edge Inspector Flyout */}
      {selectedEdge && (
        <EdgeInspector
          edge={selectedEdge}
          sourceNode={canvas.nodes.find(
            (n) => String(n.id) === String(selectedEdge.source_node_id || selectedEdge.source),
          )}
          targetNode={canvas.nodes.find(
            (n) => String(n.id) === String(selectedEdge.target_node_id || selectedEdge.target),
          )}
          onSave={handleSaveEdge}
          onDelete={handleDeleteEdge}
          onClose={() => setSelectedEdge(null)}
        />
      )}

      {/* Add Node Hierarchy Dialog */}
      <AddNodeDialog
        isOpen={showAddNodeDialog}
        availableNodes={canvas.nodes}
        initialParentId={addNodeInitialParentId}
        onConfirm={handleAddNodeConfirm}
        onCancel={() => setShowAddNodeDialog(false)}
      />

      {/* Version History Panel (FE-3) */}
      {exportHistory.isHistoryOpen && (
        <VersionHistoryPanel
          mapId={mapId}
          snapshots={exportHistory.snapshots}
          snapshotsLoading={exportHistory.snapshotsLoading}
          onClose={exportHistory.closeHistory}
          onRestore={exportHistory.restoreSnapshot}
          onDelete={exportHistory.deleteSnapshot}
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
            <h3 style={{ margin: '0 0 8px', fontFamily: 'var(--font-headline)', fontSize: '20px' }}>
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
              <div style={{ marginBottom: '12px' }}>
                <label
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    fontWeight: 600,
                    marginBottom: '4px',
                    color: 'var(--ink-secondary)',
                  }}
                >
                  Detail Level
                </label>
                <select
                  value={regenDetailLevel}
                  onChange={(e) => setRegenDetailLevel(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    borderRadius: '4px',
                    border: '1px solid var(--structural-border, #e1e3e0)',
                    backgroundColor: 'var(--surface, #ffffff)',
                  }}
                >
                  <option value="overview">Overview</option>
                  <option value="balanced">Balanced</option>
                  <option value="detailed">Detailed</option>
                </select>
              </div>
              <textarea
                rows={4}
                className="thoughts-input"
                style={{ width: '100%', marginBottom: '12px', boxSizing: 'border-box' }}
                placeholder={
                  'e.g. Expand on supply chain vulnerabilities and remove introductory overviews...'
                }
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
      {(showExportModal || exportHistory.isExportModalOpen) && (
        <ExportModal
          mapTitle={mapTitle}
          onClose={handleExportModalClose}
          onExport={handleExportConfirm}
          isExporting={isExporting}
          progress={exportProgress}
          errorMessage={exportError}
        />
      )}

      {/* Branch Deletion Confirmation Modal */}
      {deleteConfirmNode && (
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
          onClick={() => setDeleteConfirmNode(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--card-surface, #ffffff)',
              border: '1px solid var(--structural-border, #e1e3e0)',
              borderRadius: '8px',
              padding: '24px',
              maxWidth: '440px',
              width: '90%',
              boxShadow: '0 8px 30px rgba(0, 0, 0, 0.12)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
              <span
                className="material-symbols-outlined"
                style={{ color: '#ba1a1a', fontSize: '24px' }}
              >
                warning
              </span>
              <h3
                style={{
                  margin: 0,
                  fontFamily: 'var(--font-headline)',
                  fontSize: '18px',
                  color: 'var(--on-surface, #181d1a)',
                }}
              >
                Delete Branch?
              </h3>
            </div>
            <p
              style={{
                margin: '0 0 16px',
                color: 'var(--ink-secondary, #404947)',
                fontSize: '13px',
                lineHeight: 1.5,
              }}
            >
              Concept <strong>"{deleteConfirmNode.label}"</strong> has{' '}
              <strong>{deleteConfirmNode.descendantCount}</strong> connected sub-concept
              {deleteConfirmNode.descendantCount > 1 ? 's' : ''}. Deleting it will remove the entire
              branch and all associated connections.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setDeleteConfirmNode(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                style={{
                  backgroundColor: '#ba1a1a',
                  color: '#ffffff',
                  border: 'none',
                  padding: '8px 16px',
                  borderRadius: '4px',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
                onClick={handleConfirmDeleteBranch}
              >
                Delete Branch
              </button>
            </div>
          </div>
        </div>
      )}
      {/* S3-FE3: Web Research Degradation Notice Banner */}
      {isResearchDegraded && (
        <ResearchDegradedBanner
          reason={initialMetadata?.degraded_reason || 'Web research degraded during generation.'}
          onDismiss={() => setResearchNoticeDismissed(true)}
        />
      )}

      {/* S3-FE3: Aggregated Map Sources & Research Transparency Sidebar */}
      <MapSourcesSidebar
        isOpen={showSourcesSidebar}
        onClose={() => setShowSourcesSidebar(false)}
        nodes={canvas.nodes}
        onSelectNode={(node) => {
          handleNodeSelect(node)
          const hasPos = node.pos_x !== undefined || node.x !== undefined
          if (flowApiRef.current?.setCenter && hasPos) {
            const nx = Number(node.pos_x ?? node.x ?? 0)
            const ny = Number(node.pos_y ?? node.y ?? 0)
            if (nx !== 0 || ny !== 0) {
              flowApiRef.current.setCenter(nx, ny, { duration: 500 })
            }
          }
        }}
      />

      {/* S2-FE4: Conversational Chat Panel (US-14) */}
      <ChatPanel
        isOpen={commentsChat.isChatOpen}
        onClose={() => commentsChat.toggleChat(false)}
        messages={commentsChat.messages}
        isSending={commentsChat.isSending}
        chatError={commentsChat.chatError}
        onSendMessage={commentsChat.sendChatMessage}
        onRetry={commentsChat.retryLastMessage}
        onConfirmAction={handleConfirmChatAction}
        onDismissAction={handleDismissChatAction}
        selectedNode={canvas.selectedNode}
        nodes={canvas.nodes}
        onSelectNode={(nodeOrId) => {
          const nodeObj =
            typeof nodeOrId === 'string' ? canvas.nodes?.find((n) => n.id === nodeOrId) : nodeOrId
          if (nodeObj) {
            canvas.selectNode?.(nodeObj)
            if (
              flowApiRef.current?.setCenter &&
              (nodeObj.pos_x !== undefined || nodeObj.x !== undefined)
            ) {
              const nx = Number(nodeObj.pos_x ?? nodeObj.x ?? 0)
              const ny = Number(nodeObj.pos_y ?? nodeObj.y ?? 0)
              if (nx !== 0 || ny !== 0) {
                flowApiRef.current.setCenter(nx, ny, { duration: 500 })
              }
            }
          }
        }}
      />
    </div>
  )
}

export default EditorWorkspace
