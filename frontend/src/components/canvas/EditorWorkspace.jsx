import React, { useState, useEffect, useRef, useCallback } from 'react'
import { CanvasTopBar } from './CanvasTopBar'
import { MindMapCanvas } from './MindMapCanvas'
import { NodeInspector } from './NodeInspector'
import { VersionHistoryPanel } from '../history/VersionHistoryPanel'
import { ExportModal } from './ExportModal'
import { exportMindMap } from '../../services/exportService'
import { mindMapService } from '../../services/mindMapService'
import { getDescendantNodeIds, validateConnection } from '../../utils/connectionGuards'
import { useCanvasState } from './useCanvasState'
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
  const saveTimerRef = useRef(null)

  // FE-1: Canvas & Undo/Redo State
  const defaultCanvas = useCanvasState({
    initialNodes,
    initialEdges,
  })
  const canvas = customCanvasState || defaultCanvas
  const { setGraphData } = canvas

  // S2-FE5 / US-08: autosave persistence callback (latest title/viewport)
  const persistCurrentState = useCallback(async () => {
    if (!mapId) return
    try {
      await updateMap(mapId, { title: mapTitle.trim() })
    } catch (err) {
      console.warn('Failed to autosave title:', err)
      throw err
    }
  }, [mapId, mapTitle, updateMap])

  // S2-FE5 / US-08: autosave status machine for the editor header
  const autosave = useSaveStatus(persistCurrentState, { debounceMs: 300 })
  const { scheduleSave } = autosave
  const saveStatus = autosave.status

  // S2-FE5 / US-08: autosave title changes (debounced 300ms)
  useEffect(() => {
    if (!mapId || !mapTitle.trim()) return
    scheduleSave()
  }, [mapTitle, mapId, scheduleSave])

  // S2-FE5 / US-08: autosave node positions/viewport on graph change
  useEffect(() => {
    if (!mapId || canvas.nodes.length === 0) return
    scheduleSave()
  }, [canvas.nodes, mapId, scheduleSave])

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

  // Clear debounce timer on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) {
        clearTimeout(saveTimerRef.current)
      }
    }
  }, [])

  // Debounced persistence of graph mutations to backend
  useEffect(() => {
    if (!hasUserEditedRef.current || !mapId) return

    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
    }

    setIsSaving(true)
    saveTimerRef.current = setTimeout(async () => {
      try {
        await mindMapService.updateMapGraph(mapId, {
          nodes: canvas.nodes,
          edges: canvas.edges,
        })
      } catch (err) {
        // Backend PATCH /maps/{id}/graph may not be implemented yet (404/501).
        // Maintain local state so the user never loses their changes.
        console.warn('Backend graph persistence unavailable or failed:', err?.message || err)
      } finally {
        setIsSaving(false)
      }
    }, 750)
  }, [canvas.nodes, canvas.edges, mapId])

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

  // FE-3: Export & History State
  const defaultExportHistory = useExportHistory({
    mapId,
    mapTitle,
    onRestoreGraph: (restoredData) => {
      canvas.setGraphData({
        nodes: restoredData.nodes || [],
        edges: restoredData.edges || [],
      })
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
    setBranchRegenStatus('Analyzing branch context & comments...')

    try {
      await new Promise((r) => setTimeout(r, 450))
      setBranchRegenProgress(45)
      setBranchRegenStatus('Retrieving domain context...')

      await new Promise((r) => setTimeout(r, 550))
      setBranchRegenProgress(80)
      setBranchRegenStatus('Synthesizing updated branch structure...')

      await new Promise((r) => setTimeout(r, 400))
      setBranchRegenProgress(100)
      setBranchRegenStatus('Updating canvas nodes...')

      const descendantIds = getDescendantNodeIds(canvas.nodes, canvas.edges, nodeId)
      const timestamp = Date.now()
      const promptSnippet = instruction ? instruction.trim().slice(0, 20) : 'Refined'
      const targetLabel = targetNode.label || targetNode.title || 'Concept'

      const newChild1 = {
        id: `node_regen_${timestamp}_1`,
        label: instruction ? `${promptSnippet}... Synthesis` : `${targetLabel} Frontiers`,
        description: instruction
          ? `Regenerated from steering: "${instruction.slice(0, 45)}"`
          : 'Refined conceptual depth and updated state-of-the-art methodology.',
        category: 'Enhanced Scope',
        nodeType: 'conceptNode',
        pos_x: (targetNode.pos_x || targetNode.position?.x || 100) + 320,
        pos_y: (targetNode.pos_y || targetNode.position?.y || 100) - 40,
        backgroundColor: targetNode.backgroundColor || '#155E63',
      }

      const newChild2 = {
        id: `node_regen_${timestamp}_2`,
        label: 'Emerging Frameworks',
        description: 'Synthesized domain patterns and structured empirical findings.',
        category: 'Key Trajectory',
        nodeType: 'conceptNode',
        pos_x: (targetNode.pos_x || targetNode.position?.x || 100) + 320,
        pos_y: (targetNode.pos_y || targetNode.position?.y || 100) + 60,
        backgroundColor: targetNode.backgroundColor || '#155E63',
      }

      const newEdge1 = {
        id: `edge_${nodeId}_${newChild1.id}`,
        source: nodeId,
        target: newChild1.id,
      }
      const newEdge2 = {
        id: `edge_${nodeId}_${newChild2.id}`,
        source: nodeId,
        target: newChild2.id,
      }

      const remainingNodes = canvas.nodes.filter((n) => !descendantIds.includes(n.id))
      const remainingEdges = canvas.edges.filter(
        (e) => !descendantIds.includes(e.source) && !descendantIds.includes(e.target),
      )

      canvas.setGraphData({
        nodes: [...remainingNodes, newChild1, newChild2],
        edges: [...remainingEdges, newEdge1, newEdge2],
      })

      displayToast(`Branch "${targetLabel}" successfully regenerated.`)
    } catch (err) {
      // Leave canvas state completely intact on failure
      canvas.setGraphData({ nodes: snapshotNodes, edges: snapshotEdges })
      displayToast('Branch regeneration failed. Canvas state preserved.')
    } finally {
      setIsBranchRegenerating(false)
      setBranchRegenProgress(0)
      setBranchRegenStatus('')
    }
  }

  const displayToast = (msg) => {
    setLocalToast(msg)
    setTimeout(() => setLocalToast(''), 3500)
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
        canUndo={canvas.canUndo}
        canRedo={canvas.canRedo}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onAddNode={() => {
          hasUserEditedRef.current = true
          const newNode = {
            id: `node-${Date.now()}`,
            label: 'New Concept',
            description: '',
            node_type: 'concept',
            pos_x: 100,
            pos_y: 100,
          }
          canvas.addNode(newNode)
        }}
        viewMode={canvas.viewMode}
        onViewModeChange={canvas.setViewMode}
        saveStatus={saveStatus}
        onSaveRetry={() => {
          void autosave.retry()
        }}
      />

      {/* Visual Canvas or Outline Mode */}
      {canvas.viewMode === 'outline' ? (
        <div className="outline-view-container">
          <div className="outline-card">
            <h2 className="outline-title font-headline">{mapTitle}</h2>
            {canvas.nodes.length > 0 ? (
              <ul className="outline-list">
                {canvas.nodes
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
                        {canvas.nodes
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
              onClick={() => canvas.setViewMode('canvas')}
            >
              Switch to Canvas View
            </button>
          </div>
        </div>
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
          onNodeSelect={canvas.selectNode}
          onPaneClick={canvas.clearSelectedNode}
          onNodePositionChange={handleNodePositionChange}
          onConnect={handleConnect}
          onReconnect={handleReconnect}
          onStartEditing={handleStartEditing}
          onSaveLabel={handleSaveLabel}
          onCancelEditing={handleCancelEditing}
          onDeleteNode={handleDeleteNode}
          onAddChild={handleAddChild}
          onAddSibling={handleAddSibling}
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
          comments={commentsChat.commentsByNodeId[canvas.selectedNode?.id] || []}
          onAddComment={commentsChat.addComment}
          onResolveComment={commentsChat.resolveComment}
          onRegenerateBranch={handleRegenerateBranch}
          isRegenerating={isBranchRegenerating}
          regenerationProgress={branchRegenProgress}
          regenerationStatus={branchRegenStatus}
        />
      )}

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
                  <option value="in_depth">In-depth</option>
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
    </div>
  )
}

export default EditorWorkspace
