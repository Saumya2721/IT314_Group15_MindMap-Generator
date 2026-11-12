import { useReducer, useCallback } from 'react'
import { mindMapService } from '../../services/mindMapService'

export const INITIAL_EXPORT_HISTORY_STATE = {
  isExportModalOpen: false,
  isExporting: false,
  exportProgress: 0,
  exportError: '',
  isHistoryOpen: false,
  snapshots: [],
  snapshotsLoading: false,
  historyError: '',
}

export function exportHistoryReducer(state, action) {
  switch (action.type) {
    case 'OPEN_EXPORT':
      return {
        ...state,
        isExportModalOpen: true,
        exportProgress: 0,
        exportError: '',
      }
    case 'CLOSE_EXPORT':
      return {
        ...state,
        isExportModalOpen: false,
        exportProgress: 0,
        exportError: '',
      }
    case 'SET_EXPORTING':
      return { ...state, isExporting: action.payload }
    case 'SET_EXPORT_PROGRESS':
      return { ...state, exportProgress: action.payload }
    case 'SET_EXPORT_ERROR':
      return { ...state, exportError: action.payload, isExporting: false }
    case 'OPEN_HISTORY':
      return { ...state, isHistoryOpen: true, historyError: '' }
    case 'CLOSE_HISTORY':
      return { ...state, isHistoryOpen: false, historyError: '' }
    case 'SET_SNAPSHOTS':
      return { ...state, snapshots: action.payload, snapshotsLoading: false }
    case 'SET_SNAPSHOTS_LOADING':
      return { ...state, snapshotsLoading: action.payload }
    case 'SET_HISTORY_ERROR':
      return { ...state, historyError: action.payload, snapshotsLoading: false }
    case 'DELETE_SNAPSHOT':
      return {
        ...state,
        snapshots: state.snapshots.filter((s) => s.id !== action.payload),
      }
    default:
      return state
  }
}

/**
 * useExportHistory (FE-3: Export and History)
 *
 * Isolates export dialog state, progress tracking, and version history / snapshot
 * restoration so FE-3 can be developed independently without merge conflicts.
 */
export function useExportHistory({
  mapId = null,
  mapTitle = 'Mind Map',
  onRestoreGraph = null,
} = {}) {
  const [state, dispatch] = useReducer(exportHistoryReducer, INITIAL_EXPORT_HISTORY_STATE)

  const openExport = useCallback(() => {
    dispatch({ type: 'OPEN_EXPORT' })
  }, [])

  const closeExport = useCallback(() => {
    if (state.isExporting) return
    dispatch({ type: 'CLOSE_EXPORT' })
  }, [state.isExporting])

  const executeJsonExport = useCallback(
    (title = mapTitle, nodes = [], edges = []) => {
      try {
        const exportData = {
          title,
          nodes,
          edges,
          exportedAt: new Date().toISOString(),
        }
        const blob = new Blob([JSON.stringify(exportData, null, 2)], {
          type: 'application/json',
        })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `${(title || 'mindmap').toLowerCase().replace(/\s+/g, '_')}_graph.json`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
        return { success: true, format: 'json' }
      } catch (err) {
        dispatch({ type: 'SET_EXPORT_ERROR', payload: err?.message || 'Export failed' })
        throw err
      }
    },
    [mapTitle],
  )

  const openHistory = useCallback(async () => {
    dispatch({ type: 'OPEN_HISTORY' })
    if (mapId) {
      dispatch({ type: 'SET_SNAPSHOTS_LOADING', payload: true })
      try {
        const data = await mindMapService.getSnapshots(mapId)
        dispatch({ type: 'SET_SNAPSHOTS', payload: data })
      } catch (err) {
        dispatch({
          type: 'SET_HISTORY_ERROR',
          payload: err?.message || 'Failed to load snapshots',
        })
      }
    }
  }, [mapId])

  const closeHistory = useCallback(() => {
    dispatch({ type: 'CLOSE_HISTORY' })
  }, [])

  const loadSnapshots = useCallback(
    async (targetMapId = mapId) => {
      if (!targetMapId) return []
      dispatch({ type: 'SET_SNAPSHOTS_LOADING', payload: true })
      try {
        const data = await mindMapService.getSnapshots(targetMapId)
        dispatch({ type: 'SET_SNAPSHOTS', payload: data })
        return data
      } catch (err) {
        dispatch({
          type: 'SET_HISTORY_ERROR',
          payload: err?.message || 'Failed to load snapshots',
        })
        return []
      }
    },
    [mapId],
  )

  const restoreSnapshot = useCallback(
    async (targetMapId = mapId, snapshotId) => {
      if (!targetMapId || !snapshotId) return null
      try {
        const res = await mindMapService.restoreSnapshot(targetMapId, snapshotId)
        const restoredGraph = res.snapshot_data || { nodes: [], edges: [] }
        if (onRestoreGraph) {
          onRestoreGraph(restoredGraph)
        }
        return restoredGraph
      } catch (err) {
        dispatch({
          type: 'SET_HISTORY_ERROR',
          payload: err?.message || 'Failed to restore snapshot',
        })
        throw err
      }
    },
    [mapId, onRestoreGraph],
  )

  const deleteSnapshot = useCallback(
    async (targetMapId = mapId, snapshotId) => {
      if (!targetMapId || !snapshotId) return
      try {
        await mindMapService.deleteSnapshot(targetMapId, snapshotId)
        dispatch({ type: 'DELETE_SNAPSHOT', payload: snapshotId })
      } catch (err) {
        dispatch({
          type: 'SET_HISTORY_ERROR',
          payload: err?.message || 'Failed to delete snapshot',
        })
        throw err
      }
    },
    [mapId],
  )

  return {
    isExportModalOpen: state.isExportModalOpen,
    isExporting: state.isExporting,
    exportProgress: state.exportProgress,
    exportError: state.exportError,
    openExport,
    closeExport,
    executeJsonExport,
    isHistoryOpen: state.isHistoryOpen,
    snapshots: state.snapshots,
    snapshotsLoading: state.snapshotsLoading,
    historyError: state.historyError,
    openHistory,
    closeHistory,
    loadSnapshots,
    restoreSnapshot,
    deleteSnapshot,
  }
}

export default useExportHistory
