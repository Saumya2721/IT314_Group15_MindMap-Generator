import React, { useMemo, useState } from 'react'
import { ReactFlow, Background, Controls, MiniMap, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { adaptBackendGraphToFlow } from '../../utils/graphAdapter'
import { formatRelativeTime } from '../../utils/formatters'
import { Button } from '../common/Button'
import './VersionHistoryPanel.css'

const TRIGGER_LABELS = {
  initial_generation: 'Initial Generation',
  full_regen: 'Full Regeneration',
  branch_regen: 'Branch Regeneration',
  manual: 'Manual Snapshot',
}

function SnapshotFlowPreview({ snapshotData }) {
  const { flowNodes, flowEdges } = useMemo(
    () => adaptBackendGraphToFlow(snapshotData?.nodes || [], snapshotData?.edges || []),
    [snapshotData?.nodes, snapshotData?.edges],
  )

  if (!flowNodes || flowNodes.length === 0) {
    return (
      <div className="vhp-preview-empty">
        <span className="material-symbols-outlined" style={{ fontSize: '22px' }}>
          graph
        </span>
        <p>This snapshot has no graph nodes.</p>
      </div>
    )
  }

  return (
    <div className="vhp-preview-canvas">
      <ReactFlowProvider>
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.2}
          maxZoom={2}
          defaultEdgeOptions={{ type: 'smoothstep' }}
        >
          <Background color="#b0b8b6" gap={24} size={1} />
          <Controls showInteractive={false} position="bottom-right" />
          <MiniMap
            nodeStrokeColor="#155E63"
            maskColor="rgba(240, 245, 243, 0.75)"
            position="bottom-left"
          />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  )
}

function RestoreConfirm({ snapshot, onConfirm, onCancel }) {
  const nodeCount = Array.isArray(snapshot?.snapshot_data?.nodes)
    ? snapshot.snapshot_data.nodes.length
    : 0
  const edgeCount = Array.isArray(snapshot?.snapshot_data?.edges)
    ? snapshot.snapshot_data.edges.length
    : 0

  return (
    <div className="vhp-modal-backdrop" onClick={onCancel}>
      <div
        className="vhp-modal vhp-modal-restore"
        role="dialog"
        aria-modal="true"
        aria-label="Restore snapshot"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="vhp-modal-header">
          <h4>Restore Version {snapshot.version_number}</h4>
          <button type="button" className="vhp-close" onClick={onCancel} aria-label="Close">
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              close
            </span>
          </button>
        </div>
        <p className="vhp-modal-body">
          Restoring this snapshot will replace your current graph with <strong>{nodeCount}</strong>{' '}
          nodes and <strong>{edgeCount}</strong> edges from{' '}
          <strong>{formatRelativeTime(snapshot.created_at)}</strong>. This action cannot be undone.
        </p>
        <div className="vhp-modal-actions">
          <Button variant="secondary" className="vhp-cancel" onClick={onCancel}>
            Cancel
          </Button>
          <Button className="vhp-confirm" onClick={onConfirm}>
            Restore Version
          </Button>
        </div>
      </div>
    </div>
  )
}

function DeleteConfirm({ snapshot, onConfirm, onCancel }) {
  return (
    <div className="vhp-modal-backdrop" onClick={onCancel}>
      <div
        className="vhp-modal vhp-modal-delete"
        role="dialog"
        aria-modal="true"
        aria-label="Delete snapshot"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="vhp-modal-header">
          <h4>Delete Version {snapshot.version_number}</h4>
          <button type="button" className="vhp-close" onClick={onCancel} aria-label="Close">
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              close
            </span>
          </button>
        </div>
        <p className="vhp-modal-body">Permanently delete this snapshot? This cannot be undone.</p>
        <div className="vhp-modal-actions">
          <Button variant="secondary" className="vhp-cancel" onClick={onCancel}>
            Cancel
          </Button>
          <Button className="vhp-confirm vhp-confirm-delete" onClick={onConfirm}>
            Delete
          </Button>
        </div>
      </div>
    </div>
  )
}

export function VersionHistoryPanel({
  mapId,
  snapshots,
  snapshotsLoading = false,
  onClose,
  onRestore,
  onDelete,
  onCreateSnapshot,
}) {
  const [previewId, setPreviewId] = useState(null)
  const [confirmRestoreId, setConfirmRestoreId] = useState(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [restoringId, setRestoringId] = useState(null)
  const [actionError, setActionError] = useState('')

  const sorted = useMemo(() => {
    const arr = Array.isArray(snapshots) ? [...snapshots] : []
    return arr.sort((a, b) => (b.version_number || 0) - (a.version_number || 0))
  }, [snapshots])

  const previewSnapshot = useMemo(
    () => sorted.find((s) => s && s.id === previewId) || null,
    [sorted, previewId],
  )

  const confirmRestoreSnapshot = useMemo(
    () => sorted.find((s) => s && s.id === confirmRestoreId) || null,
    [sorted, confirmRestoreId],
  )

  const confirmDeleteSnapshot = useMemo(
    () => sorted.find((s) => s && s.id === confirmDeleteId) || null,
    [sorted, confirmDeleteId],
  )

  const handleRestore = async () => {
    if (!confirmRestoreId) return
    setRestoringId(confirmRestoreId)
    setActionError('')
    try {
      const result = await onRestore(mapId, confirmRestoreId)
      setRestoringId(null)
      setConfirmRestoreId(null)
      if (onClose) onClose()
    } catch (err) {
      setRestoringId(null)
      setActionError(err?.message || 'Failed to restore snapshot')
    }
  }

  const handleDelete = async () => {
    if (!confirmDeleteId) return
    setActionError('')
    try {
      if (onDelete) await onDelete(mapId, confirmDeleteId)
      if (confirmDeleteId === previewId) setPreviewId(null)
      setConfirmDeleteId(null)
    } catch (err) {
      setActionError(err?.message || 'Failed to delete snapshot')
    }
  }

  const renderTriggerChip = (s) => (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: '999px',
        backgroundColor: 'var(--surface-container-low, #f2f5f4)',
        color: 'var(--ink-secondary, #5e6765)',
        fontSize: '10.5px',
        fontWeight: 600,
        fontFamily: 'var(--font-label, sans-serif)',
      }}
    >
      {TRIGGER_LABELS[s?.trigger_source] || s?.trigger_source || 'Manual'}
    </span>
  )

  const renderRowActions = (s) => {
    const isRestoring = restoringId === s?.id
    return (
      <div className="vhp-item-actions">
        <button
          type="button"
          className="vhp-icon-btn"
          title="Preview this version"
          aria-label="Preview version"
          onClick={() => setPreviewId(previewId === s.id ? null : s.id)}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            visibility
          </span>
        </button>
        {confirmRestoreId === s.id || confirmDeleteId === s.id ? null : (
          <button
            type="button"
            className="vhp-icon-btn vhp-icon-btn-primary"
            title="Restore this version"
            aria-label="Restore version"
            disabled={isRestoring}
            onClick={() => setConfirmRestoreId(s.id)}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              history
            </span>
          </button>
        )}
        <button
          type="button"
          className="vhp-icon-btn vhp-icon-btn-danger"
          title="Delete this version"
          aria-label="Delete version"
          onClick={() => setConfirmDeleteId(s.id)}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            delete_outline
          </span>
        </button>
        {isRestoring ? <span className="vhp-restoring">Restoring...</span> : null}
      </div>
    )
  }

  const renderBody = () => {
    if (snapshotsLoading) {
      return (
        <div className="vhp-empty">
          <span className="material-symbols-outlined vhp-spin">sync</span>
          <p>Loading version history...</p>
        </div>
      )
    }

    if (!Array.isArray(snapshots) || snapshots.length === 0) {
      return (
        <div className="vhp-empty">
          <span
            className="material-symbols-outlined"
            style={{ fontSize: '30px', color: 'var(--outline)' }}
          >
            history
          </span>
          <h4>No versions yet</h4>
          <p>
            Once your first map is generated, a baseline snapshot is created automatically. You can
            also capture a manual snapshot at any time.
          </p>
          {typeof onCreateSnapshot === 'function' && (
            <Button
              variant="secondary"
              className="vhp-action-btn"
              onClick={() => onCreateSnapshot(mapId)}
              disabled={!mapId}
            >
              Save Current Version
            </Button>
          )}
        </div>
      )
    }

    return (
      <div className="vhp-list">
        {sorted.map((s) => {
          const nodeCount = Array.isArray(s?.snapshot_data?.nodes)
            ? s.snapshot_data.nodes.length
            : 0
          const edgeCount = Array.isArray(s?.snapshot_data?.edges)
            ? s.snapshot_data.edges.length
            : 0
          const isPreviewing = previewId === s?.id

          return (
            <div
              key={s.id}
              className={`vhp-item ${isPreviewing ? 'vhp-item-active' : ''}`}
              data-testid="version-history-item"
            >
              <div className="vhp-item-main">
                <div className="vhp-item-head">
                  <span className="vhp-version-badge">V{s.version_number}</span>
                  {renderTriggerChip(s)}
                </div>
                <div className="vhp-item-title">{s.label || `Version ${s.version_number}`}</div>
                <div className="vhp-item-meta">
                  {s.created_at ? formatRelativeTime(s.created_at) : ''} - {nodeCount} nodes -{' '}
                  {edgeCount} edges
                </div>
              </div>
              {renderRowActions(s)}
            </div>
          )
        })}
      </div>
    )
  }

  return (
    <div className="vhp-drawer-overlay" data-testid="version-history-panel">
      {/* Restore confirmation modal */}
      {confirmRestoreId && confirmRestoreSnapshot && (
        <RestoreConfirm
          snapshot={confirmRestoreSnapshot}
          onConfirm={handleRestore}
          onCancel={() => setConfirmRestoreId(null)}
        />
      )}

      {/* Delete confirmation modal */}
      {confirmDeleteId && confirmDeleteSnapshot && (
        <DeleteConfirm
          snapshot={confirmDeleteSnapshot}
          onConfirm={handleDelete}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}

      <div className="vhp-drawer" role="dialog" aria-label="Version History Panel">
        {/* Header */}
        <div className="vhp-header">
          <div className="vhp-header-left">
            <h3 className="vhp-title">Version History</h3>
            {typeof onCreateSnapshot === 'function' && (
              <Button
                variant="secondary"
                className="vhp-action-btn"
                onClick={() => onCreateSnapshot(mapId)}
                disabled={!mapId}
              >
                Save Current Version
              </Button>
            )}
          </div>
          <button
            type="button"
            className="vhp-icon-btn"
            onClick={onClose}
            aria-label="Close version history"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              close
            </span>
          </button>
        </div>

        {/* Preview state replaces list body */}
        {previewId && previewSnapshot ? (
          <div className="vhp-preview">
            <div className="vhp-preview-header">
              <span className="vhp-preview-title">Preview - V{previewSnapshot.version_number}</span>
              <button
                type="button"
                className="vhp-icon-btn"
                title="Restore this version"
                onClick={() => setConfirmRestoreId(previewSnapshot.id)}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                  history
                </span>
              </button>
              <button
                type="button"
                className="vhp-icon-btn"
                onClick={() => setPreviewId(null)}
                aria-label="Close preview"
              >
                <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                  close
                </span>
              </button>
            </div>
            <SnapshotFlowPreview snapshotData={previewSnapshot.snapshot_data} />
          </div>
        ) : (
          renderBody()
        )}

        {/* Action error */}
        {actionError ? <div className="vhp-error">{actionError}</div> : null}
      </div>
    </div>
  )
}

export default VersionHistoryPanel
