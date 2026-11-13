import React, { useState, useEffect } from 'react'
import './NodeInspector.css'

export function NodeInspector({
  selectedNode,
  onClose,
  onUpdateLabel,
  onUpdateDescription,
  onAddChild,
  onAddSibling,
  onDeleteNode,
  comments = [],
  onAddComment,
  onResolveComment,
  onRegenerateBranch,
  isRegenerating = false,
  regenerationProgress = 0,
  regenerationStatus = '',
}) {
  const [labelDraft, setLabelDraft] = useState('')
  const [descDraft, setDescDraft] = useState('')
  const [instruction, setInstruction] = useState('')
  const [newCommentText, setNewCommentText] = useState('')
  const [commentFilter, setCommentFilter] = useState('all') // 'all' | 'active' | 'resolved'

  useEffect(() => {
    if (selectedNode) {
      setLabelDraft(selectedNode.title || selectedNode.label || '')
      setDescDraft(selectedNode.description || selectedNode.subtitle || '')
    }
  }, [selectedNode])

  if (!selectedNode) return null

  const currentTint = selectedNode.tint || selectedNode.backgroundColor || '#155E63'
  const nodeType = selectedNode.nodeType || selectedNode.type || 'concept'
  const isRoot = nodeType === 'root' || nodeType === 'rootNode'
  const category = selectedNode.category || (isRoot ? 'Central Theme' : 'Concept')

  // Filter comments
  const filteredComments = (comments || []).filter((c) => {
    if (commentFilter === 'active') return !c.resolved
    if (commentFilter === 'resolved') return c.resolved
    return true
  })

  const activeCommentsCount = (comments || []).filter((c) => !c.resolved).length

  const handleLabelBlur = () => {
    if (labelDraft.trim() && labelDraft.trim() !== (selectedNode.title || selectedNode.label)) {
      onUpdateLabel?.(selectedNode.id, labelDraft.trim())
    }
  }

  const handleDescBlur = () => {
    if (descDraft !== (selectedNode.description || selectedNode.subtitle || '')) {
      onUpdateDescription?.(selectedNode.id, descDraft)
    }
  }

  const handleRegenerateClick = () => {
    if (onRegenerateBranch && !isRegenerating) {
      onRegenerateBranch(selectedNode.id, instruction)
    }
  }

  const handleAddCommentSubmit = (e) => {
    e.preventDefault()
    if (!newCommentText.trim()) return
    onAddComment?.(selectedNode.id, newCommentText.trim())
    setNewCommentText('')
  }

  return (
    <div className="node-inspector-flyout" role="complementary" aria-label="Node Inspector">
      {/* Header */}
      <div className="inspector-header">
        <div className="inspector-header-left">
          <span className="dot" style={{ backgroundColor: currentTint }} />
          <span className="inspector-node-title">
            {selectedNode.title || selectedNode.label || 'Selected Node'}
          </span>
        </div>
        <button
          type="button"
          className="btn-close-inspector"
          onClick={onClose}
          aria-label="Close inspector"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            close
          </span>
        </button>
      </div>

      {/* Category / Type Badge */}
      <div style={{ marginBottom: '14px' }}>
        <span
          style={{
            display: 'inline-block',
            padding: '3px 8px',
            borderRadius: '4px',
            backgroundColor: `${currentTint}18`,
            color: currentTint,
            fontWeight: 600,
            fontSize: '11px',
            fontFamily: 'var(--font-label, sans-serif)',
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {category}
        </span>
      </div>

      {/* Editable Node Title */}
      <div className="inspector-field">
        <label className="inspector-field-label">Concept Title</label>
        <input
          type="text"
          value={labelDraft}
          onChange={(e) => setLabelDraft(e.target.value)}
          onBlur={handleLabelBlur}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.currentTarget.blur()
            }
          }}
          style={{
            width: '100%',
            padding: '8px 10px',
            borderRadius: '6px',
            border: '1px solid var(--structural-border, #e1e3e0)',
            fontSize: '14px',
            fontFamily: 'var(--font-headline, serif)',
            color: 'var(--on-surface, #181d1a)',
            fontWeight: 500,
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            boxSizing: 'border-box',
          }}
          aria-label="Edit node title"
        />
      </div>

      {/* Editable Description */}
      <div className="inspector-field" style={{ marginTop: '12px' }}>
        <label className="inspector-field-label">Description &amp; Reasoning</label>
        <textarea
          rows={3}
          value={descDraft}
          onChange={(e) => setDescDraft(e.target.value)}
          onBlur={handleDescBlur}
          placeholder="Add detailed explanation or reasoning for this node..."
          style={{
            width: '100%',
            padding: '8px 10px',
            borderRadius: '6px',
            border: '1px solid var(--structural-border, #e1e3e0)',
            fontSize: '13px',
            lineHeight: 1.5,
            fontFamily: 'var(--font-body, serif)',
            color: 'var(--on-surface-variant, #404947)',
            backgroundColor: 'var(--surface-container-low, #f2f5f4)',
            boxSizing: 'border-box',
            resize: 'vertical',
          }}
          aria-label="Edit node description"
        />
      </div>

      {/* Quick Actions (Add Child, Add Sibling, Delete) */}
      <div style={{ marginTop: '16px' }}>
        <label className="inspector-field-label">Node Actions</label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}>
          {/* Add Child Node */}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => onAddChild?.(selectedNode.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              fontSize: '12.5px',
              padding: '7px 12px',
              width: '100%',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              subdirectory_arrow_right
            </span>
            <span>Add Child Concept (Tab)</span>
          </button>

          {/* Add Sibling Node (allowed for non-root nodes) */}
          {!isRoot && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => onAddSibling?.(selectedNode.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                fontSize: '12.5px',
                padding: '7px 12px',
                width: '100%',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                add_circle_outline
              </span>
              <span>Add Sibling Concept (Enter)</span>
            </button>
          )}

          {/* Delete Node (allowed for non-root nodes) */}
          {!isRoot && (
            <button
              type="button"
              onClick={() => onDeleteNode?.(selectedNode.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '6px',
                fontSize: '12px',
                padding: '7px 12px',
                width: '100%',
                borderRadius: '6px',
                border: '1px solid #ffdad6',
                backgroundColor: '#fff8f7',
                color: '#ba1a1a',
                cursor: 'pointer',
                fontFamily: 'var(--font-label, sans-serif)',
                fontWeight: 500,
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                delete_outline
              </span>
              <span>Delete Node (Del)</span>
            </button>
          )}
        </div>
      </div>

      {/* Divider */}
      <div className="inspector-divider" />

      {/* S2-FE3: Branch AI Regeneration */}
      <div className="inspector-section branch-regen-section">
        <div className="inspector-section-header">
          <div className="section-title-wrap">
            <span className="material-symbols-outlined section-icon" style={{ color: 'var(--accent-terracotta, #c96b4b)' }}>
              neurology
            </span>
            <span className="inspector-field-label">Branch AI Regeneration</span>
          </div>
          {isRoot && <span className="badge-context-pill">Root Map</span>}
        </div>

        <p className="section-help-text">
          Re-synthesize this branch and connected descendants using custom steering.
        </p>

        <div className="regen-input-wrap">
          <textarea
            className="inspector-textarea"
            rows={2}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Steering instruction (e.g. Expand with recent breakthroughs)..."
            disabled={isRegenerating}
          />
        </div>

        {isRegenerating && (
          <div className="regen-progress-container">
            <div className="regen-progress-header">
              <span className="regen-status-label">
                {regenerationStatus || 'Regenerating branch...'}
              </span>
              <span className="regen-percent-label">{Math.round(regenerationProgress)}%</span>
            </div>
            <div className="regen-progress-track">
              <div
                className="regen-progress-fill"
                style={{ width: `${Math.max(8, Math.min(100, regenerationProgress))}%` }}
              />
            </div>
          </div>
        )}

        <button
          type="button"
          className={`btn-regenerate-branch ${isRegenerating ? 'loading' : ''}`}
          onClick={handleRegenerateClick}
          disabled={isRegenerating}
        >
          <span
            className="material-symbols-outlined"
            style={{ fontSize: '16px', animation: isRegenerating ? 'spin 1s linear infinite' : 'none' }}
          >
            {isRegenerating ? 'sync' : 'auto_awesome'}
          </span>
          <span>{isRegenerating ? 'Regenerating Branch...' : 'Regenerate Branch'}</span>
        </button>
      </div>

      {/* Divider */}
      <div className="inspector-divider" />

      {/* S2-FE3: Node Comments Thread Panel */}
      <div className="inspector-section comments-section">
        <div className="inspector-section-header">
          <div className="section-title-wrap">
            <span className="material-symbols-outlined section-icon" style={{ color: 'var(--accent-teal, #155e63)' }}>
              forum
            </span>
            <span className="inspector-field-label">Node Discussion</span>
          </div>
          <div className="comments-header-badges">
            {activeCommentsCount > 0 && (
              <span className="comments-count-badge" title="Active comments">
                {activeCommentsCount} open
              </span>
            )}
          </div>
        </div>

        {/* Filter Tabs */}
        {(comments || []).length > 0 && (
          <div className="comments-filter-tabs">
            <button
              type="button"
              className={`btn-comment-tab ${commentFilter === 'all' ? 'active' : ''}`}
              onClick={() => setCommentFilter('all')}
            >
              All ({comments.length})
            </button>
            <button
              type="button"
              className={`btn-comment-tab ${commentFilter === 'active' ? 'active' : ''}`}
              onClick={() => setCommentFilter('active')}
            >
              Open ({activeCommentsCount})
            </button>
            <button
              type="button"
              className={`btn-comment-tab ${commentFilter === 'resolved' ? 'active' : ''}`}
              onClick={() => setCommentFilter('resolved')}
            >
              Resolved ({comments.length - activeCommentsCount})
            </button>
          </div>
        )}

        {/* Comment Thread List */}
        <div className="comments-thread-list">
          {filteredComments.length === 0 ? (
            <div className="comments-empty-state">
              <span className="material-symbols-outlined" style={{ fontSize: '20px', color: 'var(--outline-variant, #bfc8c9)' }}>
                chat_bubble_outline
              </span>
              <p className="empty-state-text">
                {commentFilter === 'resolved'
                  ? 'No resolved comments.'
                  : commentFilter === 'active'
                  ? 'No open comments on this node.'
                  : 'No comments yet. Leave a review note or branch idea.'}
              </p>
            </div>
          ) : (
            filteredComments.map((comment) => (
              <div
                key={comment.id}
                className={`comment-item-card ${comment.resolved ? 'resolved' : ''}`}
              >
                <div className="comment-item-top">
                  <div className="comment-author-badge">
                    <span className="author-avatar">{comment.author ? comment.author[0] : 'U'}</span>
                    <span className="comment-author-name">{comment.author || 'User'}</span>
                    <span className="comment-timestamp">{comment.createdAt || 'Just now'}</span>
                  </div>

                  <button
                    type="button"
                    className={`btn-resolve-toggle ${comment.resolved ? 'is-resolved' : ''}`}
                    onClick={() => onResolveComment?.(selectedNode.id, comment.id)}
                    title={comment.resolved ? 'Reopen comment' : 'Mark as resolved'}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                      {comment.resolved ? 'check_circle' : 'radio_button_unchecked'}
                    </span>
                    <span>{comment.resolved ? 'Resolved' : 'Resolve'}</span>
                  </button>
                </div>

                <p className="comment-body-text">{comment.text}</p>
              </div>
            ))
          )}
        </div>

        {/* Add Comment Input Form */}
        <form onSubmit={handleAddCommentSubmit} className="comment-add-form">
          <input
            type="text"
            className="inspector-input comment-input"
            placeholder="Add a comment or steering note..."
            value={newCommentText}
            onChange={(e) => setNewCommentText(e.target.value)}
          />
          <button
            type="submit"
            className="btn-submit-comment"
            disabled={!newCommentText.trim()}
            title="Post comment"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '15px' }}>
              send
            </span>
          </button>
        </form>
      </div>

      {/* Connections Info */}
      <div style={{ marginTop: '16px', fontSize: '12px', color: 'var(--outline, #6f7977)' }}>
        <div>
          Direct connections:{' '}
          {(selectedNode.incomingEdges?.length || 0) + (selectedNode.outgoingEdges?.length || 0)}{' '}
          links
        </div>
      </div>
    </div>
  )
}

export default NodeInspector
