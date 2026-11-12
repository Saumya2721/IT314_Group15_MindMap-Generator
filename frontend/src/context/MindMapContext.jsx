import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { mindMapService } from '../services/mindMapService.js'

const MindMapContext = createContext(null)
const BOOKMARKS_STORAGE_KEY = 'mindweave_bookmarked_ids'

function getStoredBookmarks() {
  try {
    const raw = localStorage.getItem(BOOKMARKS_STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function setStoredBookmarks(ids) {
  try {
    localStorage.setItem(BOOKMARKS_STORAGE_KEY, JSON.stringify(ids))
  } catch {
    // ignore
  }
}

export function MindMapProvider({ children }) {
  const [maps, setMaps] = useState([])
  const [activeMap, setActiveMap] = useState(null)
  const [, setBookmarkedIds] = useState(getStoredBookmarks)
  const [draftMap, setDraftMap] = useState({
    title: '',
    topic: '',
    thoughts: '',
    detailLevel: 'overview',
    files: [],
    filePreviews: [],
  })
  const [snapshots, setSnapshots] = useState([])
  const [snapshotsLoading, setSnapshotsLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState(null)

  const fetchMaps = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await mindMapService.getMaps()
      const currentBookmarks = new Set(getStoredBookmarks())
      const mapped = data.map((m) => ({
        ...m,
        bookmarked: currentBookmarks.has(m.id),
      }))
      setMaps(mapped)
      return mapped
    } catch (err) {
      setError(err.message || 'Failed to load maps')
      return []
    } finally {
      setLoading(false)
    }
  }, [])

  const searchMindMaps = useCallback(async (q, limit = 20) => {
    const trimmed = (q || '').trim()
    if (!trimmed) {
      setSearchResults(null)
      setSearchLoading(false)
      setSearchError(null)
      return []
    }
    setSearchLoading(true)
    setSearchError(null)
    try {
      const data = await mindMapService.searchMaps(trimmed, limit)
      const mapped = data.map((m) => ({
        ...m,
        bookmarked: getStoredBookmarks().includes(m.id),
      }))
      setSearchResults(mapped)
      return mapped
    } catch (err) {
      setSearchError(err.message || 'Failed to search mind maps')
      setSearchResults([])
      return []
    } finally {
      setSearchLoading(false)
    }
  }, [])

  const createMap = async (data) => {
    setLoading(true)
    setError(null)
    try {
      const newMap = await mindMapService.createMap(data)
      setMaps((prev) => [newMap, ...prev])
      setActiveMap(newMap)
      return newMap
    } catch (err) {
      setError(err.message || 'Failed to create map')
      throw err
    } finally {
      setLoading(false)
    }
  }

  const updateMap = async (id, updates) => {
    try {
      const updated = await mindMapService.updateMap(id, updates)
      setMaps((prev) => prev.map((m) => (m.id === id ? { ...m, ...updated } : m)))
      if (activeMap?.id === id) {
        setActiveMap((prev) => ({ ...prev, ...updated }))
      }
      return updated
    } catch (err) {
      console.error('Failed to update map:', err)
      throw err
    }
  }

  const deleteMap = async (id) => {
    try {
      await mindMapService.deleteMap(id)
      setMaps((prev) => prev.filter((m) => m.id !== id))
      if (activeMap?.id === id) {
        setActiveMap(null)
      }
    } catch (err) {
      console.error('Failed to delete map:', err)
      throw err
    }
  }

  const toggleBookmark = (id) => {
    setBookmarkedIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
      setStoredBookmarks(next)
      setMaps((mList) =>
        mList.map((m) => (m.id === id ? { ...m, bookmarked: next.includes(id) } : m)),
      )
      return next
    })
  }

  // ------------------------------------------------------------------
  // Version History / Snapshots (US-04 / S2-FE7)
  // ------------------------------------------------------------------

  const loadSnapshots = useCallback(async (mapId) => {
    setSnapshotsLoading(true)
    try {
      const data = await mindMapService.getSnapshots(mapId)
      setSnapshots(data)
      return data
    } catch (err) {
      console.error('Failed to load snapshots:', err)
      setSnapshots([])
      return []
    } finally {
      setSnapshotsLoading(false)
    }
  }, [])

  const restoreSnapshot = useCallback(async (mapId, snapshotId) => {
    const restored = await mindMapService.restoreSnapshot(mapId, snapshotId)
    return restored.snapshot_data || { nodes: [], edges: [] }
  }, [])

  useEffect(() => {
    fetchMaps()
  }, [fetchMaps])

  return (
    <MindMapContext.Provider
      value={{
        maps,
        activeMap,
        draftMap,
        setDraftMap,
        loading,
        error,
        fetchMaps,
        createMap,
        updateMap,
        deleteMap,
        toggleBookmark,
        setActiveMap,
        snapshots,
        snapshotsLoading,
        loadSnapshots,
        restoreSnapshot,
        searchQuery,
        setSearchQuery,
        searchResults,
        searchLoading,
        searchError,
        searchMindMaps,
      }}
    >
      {children}
    </MindMapContext.Provider>
  )
}

export function useMindMap() {
  const context = useContext(MindMapContext)
  if (!context) {
    throw new Error('useMindMap must be used within a MindMapProvider')
  }
  return context
}
