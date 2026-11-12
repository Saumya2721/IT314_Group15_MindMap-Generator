import React, { useState, useEffect, useRef, useCallback } from 'react'
import { CanvasTopBar } from './CanvasTopBar'
import { MindMapCanvas } from './MindMapCanvas'
import { NodeInspector } from './NodeInspector'
import { VersionHistoryPanel } from '../history/VersionHistoryPanel'
import { ExportModal } from './ExportModal'
import { exportMindMap } from '../../services/exportService'
import { useCanvasState } from './useCanvasState'
import { useCommentsChat } from './useCommentsChat'
import { useExportHistory } from './useExportHistory'
import { useMindMap } from '../../context/MindMapContext'
import { useSaveStatus } from './useSaveStatus'
import '../../pages/GeneratingPage.css'
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
        onUndo={canvas.undo}
        onRedo={canvas.redo}
        onAddNode={() => {
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
          flowApiRef={flowApiRef}
          initialViewport={initialViewport}
          onViewportChange={handleViewportChange}
        />
      )}

      {/* Node Inspector Flyout */}
      {canvas.selectedNode && (
        <NodeInspector selectedNode={canvas.selectedNode} onClose={canvas.clearSelectedNode} />
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
    </div>
  )
}

export default EditorWorkspace
