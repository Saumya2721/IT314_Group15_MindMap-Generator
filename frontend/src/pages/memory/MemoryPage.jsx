import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { memoryService } from '../../services/memoryService'
import { AppHeader } from '../../components/layout/AppHeader'
import { AppFooter } from '../../components/layout/AppFooter'
import './MemoryPage.css'

export function MemoryPageContent() {
  const [memories, setMemories] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('global') // 'global' | 'map'
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const [deleteModal, setDeleteModal] = useState({ isOpen: false, item: null })
  const [exporting, setExporting] = useState(false)

  const loadMemories = useCallback(async () => {
    setLoading(true)
    try {
      const data = await memoryService.getMemories('all')
      setMemories(Array.isArray(data) ? data : [])
    } catch (err) {
      console.error('Failed to load memories:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadMemories()
  }, [loadMemories])

  // Filter memories by current tab scope
  const filteredMemories = memories.filter((m) =>
    activeTab === 'global' ? m.scope === 'global' : m.scope === 'map',
  )

  // Start inline editing
  const handleStartEdit = (m) => {
    setEditingId(m.id)
    setEditText(m.content)
  }

  const handleCancelEdit = () => {
    setEditingId(null)
    setEditText('')
  }

  // Save inline edit
  const handleSaveEdit = async (id) => {
    if (!editText.trim()) return
    try {
      await memoryService.updateMemoryContent(id, editText.trim())
      setMemories((prev) => prev.map((m) => (m.id === id ? { ...m, content: editText.trim() } : m)))
      setEditingId(null)
      setEditText('')
    } catch (err) {
      console.error('Failed to update memory:', err)
    }
  }

  // Toggle active/inactive state
  const handleToggleActive = async (m) => {
    const nextState = !m.is_active
    try {
      await memoryService.toggleMemoryActive(m.id, nextState)
      setMemories((prev) =>
        prev.map((item) => (item.id === m.id ? { ...item, is_active: nextState } : item)),
      )
    } catch (err) {
      console.error('Failed to toggle memory:', err)
    }
  }

  // Open delete confirmation modal
  const handleOpenDelete = (m) => {
    setDeleteModal({ isOpen: true, item: m })
  }

  const handleCloseDelete = () => {
    setDeleteModal({ isOpen: false, item: null })
  }

  // Confirm delete
  const handleConfirmDelete = async () => {
    if (!deleteModal.item) return
    const id = deleteModal.item.id
    try {
      await memoryService.deleteMemory(id)
      setMemories((prev) => prev.filter((m) => m.id !== id))
    } catch (err) {
      console.error('Failed to delete memory:', err)
    } finally {
      handleCloseDelete()
    }
  }

  // GDPR JSON Export download handler
  const handleExportGdpr = async () => {
    setExporting(true)
    try {
      const bundle = await memoryService.exportAllMemories()
      const dataStr =
        'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(bundle, null, 2))
      const downloadAnchor = document.createElement('a')
      downloadAnchor.setAttribute('href', dataStr)
      downloadAnchor.setAttribute(
        'download',
        `mindweave-memory-export-${new Date().toISOString().slice(0, 10)}.json`,
      )
      document.body.appendChild(downloadAnchor)
      downloadAnchor.click()
      downloadAnchor.remove()
    } catch (err) {
      console.error('GDPR export failed:', err)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="memory-layout">
      <AppHeader />
      <main className="memory-main">
        {/* Header & GDPR Action */}
        <div className="memory-header">
          <div className="memory-header-info">
            <span className="memory-badge">
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                psychology
              </span>
              Cognitive Storage
            </span>
            <h1 className="memory-title">Agent Long-Term Memory</h1>
            <p className="memory-subtitle">
              Manage learned context, steering preferences, and architectural decisions. These
              memories guide the AI during knowledge graph synthesis. You maintain full ownership
              and privacy over all stored items.
            </p>
          </div>

          <div className="memory-header-actions">
            <button
              type="button"
              className="btn-export-gdpr"
              onClick={handleExportGdpr}
              disabled={exporting}
              title="Download GDPR-compliant JSON bundle (Article 20 Data Portability)"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                download
              </span>
              <span>{exporting ? 'Preparing Bundle...' : 'Export All Memories (GDPR)'}</span>
            </button>
          </div>
        </div>

        {/* Tab Navigation: Global vs Map Scoped */}
        <div className="memory-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'global'}
            className={`memory-tab-btn ${activeTab === 'global' ? 'active' : ''}`}
            onClick={() => setActiveTab('global')}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              public
            </span>
            Global Preferences ({memories.filter((m) => m.scope === 'global').length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'map'}
            className={`memory-tab-btn ${activeTab === 'map' ? 'active' : ''}`}
            onClick={() => setActiveTab('map')}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              account_tree
            </span>
            Map-Scoped Memories ({memories.filter((m) => m.scope === 'map').length})
          </button>
        </div>

        {/* Memory Items List */}
        <section aria-label="Memory List">
          {loading ? (
            <div className="memory-list">
              <div className="memory-skeleton-card" />
              <div className="memory-skeleton-card" />
              <div className="memory-skeleton-card" />
            </div>
          ) : filteredMemories.length === 0 ? (
            <div className="memory-empty-state">
              <span className="material-symbols-outlined memory-empty-icon">memory</span>
              <p className="memory-empty-text">
                {activeTab === 'global'
                  ? 'No global agent memories stored yet. As you steer mind maps, preferences will be recorded here.'
                  : 'No map-scoped memories recorded for individual mind maps.'}
              </p>
            </div>
          ) : (
            <div className="memory-list">
              {filteredMemories.map((m) => (
                <div
                  key={m.id}
                  className={`memory-row-card ${!m.is_active ? 'disabled' : ''}`}
                  data-testid="memory-row"
                >
                  <div className="memory-row-header">
                    <div className="memory-row-meta">
                      {/* Kind Badge: preference / decision / fact */}
                      <span className={`kind-badge ${m.kind || 'fact'}`}>{m.kind || 'fact'}</span>

                      {/* Map-Scoped Link if available */}
                      {m.scope === 'map' && m.map_name && (
                        <Link
                          to={m.map_id ? `/editor/${m.map_id}` : '/dashboard'}
                          className="map-link-pill"
                          title="Open connected mind map"
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                            launch
                          </span>
                          <span>{m.map_name}</span>
                        </Link>
                      )}
                    </div>

                    {/* Enable/Disable Toggle + Delete Action */}
                    <div className="memory-row-controls">
                      <div
                        className="memory-toggle-wrap"
                        onClick={() => handleToggleActive(m)}
                        role="switch"
                        aria-checked={m.is_active}
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === ' ' || e.key === 'Enter') {
                            e.preventDefault()
                            handleToggleActive(m)
                          }
                        }}
                      >
                        <div className={`toggle-switch-track ${m.is_active ? 'active' : ''}`}>
                          <div className="toggle-switch-thumb" />
                        </div>
                        <span>{m.is_active ? 'Active' : 'Disabled'}</span>
                      </div>

                      <button
                        type="button"
                        className="btn-memory-delete"
                        onClick={() => handleOpenDelete(m)}
                        title="Delete memory item"
                        aria-label="Delete memory"
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                          delete
                        </span>
                      </button>
                    </div>
                  </div>

                  {/* Content with Inline Editing */}
                  <div className="memory-content-area">
                    {editingId === m.id ? (
                      <div>
                        <textarea
                          rows={2}
                          className="memory-edit-textarea"
                          value={editText}
                          onChange={(e) => setEditText(e.target.value)}
                          autoFocus
                        />
                        <div className="memory-edit-actions">
                          <button
                            type="button"
                            className="btn-memory-cancel"
                            onClick={handleCancelEdit}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="btn-memory-save"
                            onClick={() => handleSaveEdit(m.id)}
                          >
                            Save Changes
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p
                        className="memory-text-display"
                        onClick={() => handleStartEdit(m)}
                        title="Click to edit memory text"
                      >
                        {m.content}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Delete Confirmation Modal */}
        {deleteModal.isOpen && (
          <div className="memory-modal-backdrop" onClick={handleCloseDelete}>
            <div className="memory-modal-dialog" onClick={(e) => e.stopPropagation()}>
              <div className="memory-modal-header">
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: '24px', color: '#ffb4ab' }}
                >
                  delete_forever
                </span>
                <h3 className="memory-modal-title">Delete Memory?</h3>
              </div>
              <p className="memory-modal-body">
                Are you sure you want to delete this agent memory? The AI will no longer use this
                preference or decision when restructuring your knowledge graphs.
              </p>
              <div className="memory-modal-actions">
                <button type="button" className="btn-modal-cancel" onClick={handleCloseDelete}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn-modal-confirm-danger"
                  style={{
                    backgroundColor: '#ba1a1a',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '8px 16px',
                    color: '#ffffff',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                  onClick={handleConfirmDelete}
                >
                  Delete Memory
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
      <AppFooter />
    </div>
  )
}

export default function MemoryPage() {
  return <MemoryPageContent />
}
