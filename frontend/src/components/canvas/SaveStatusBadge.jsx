import React from 'react'
import { SAVE_STATUS } from './useSaveStatus'
import './SaveStatusBadge.css'

/**
 * SaveStatusBadge — autosave status pill shown in the editor header.
 *
 * States (S2-FE5 / US-08 acceptance criteria):
 *   - 'Saving...'        — a save is in-flight
 *   - 'Saved'            — last save completed successfully
 *   - 'Save failed (Retry)' — last save errored; clicking retries
 */
export function SaveStatusBadge({ status, onRetry }) {
  const isError = status === SAVE_STATUS.ERROR
  const isSaving = status === SAVE_STATUS.SAVING

  const label = isError ? 'Save failed (Retry)' : isSaving ? 'Saving...' : 'Saved'
  const dotColor = isError ? '#C96B4B' : isSaving ? '#C96B4B' : '#155e63'

  const handleActivate = () => {
    if (isError && onRetry) onRetry()
  }

  const handleKeyDown = (e) => {
    if (isError && onRetry && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      onRetry()
    }
  }

  return (
    <span
      className={`canvas-save-status ${isError ? 'is-error' : ''} ${isSaving ? 'is-saving' : ''}`}
      role={isError ? 'button' : 'status'}
      aria-label={isError ? 'Save failed. Retry saving.' : label}
      tabIndex={isError ? 0 : -1}
      title={isError ? 'Click to retry saving' : undefined}
      onClick={handleActivate}
      onKeyDown={handleKeyDown}
    >
      <span className="save-dot" style={{ backgroundColor: dotColor }} aria-hidden="true" />
      <span>{label}</span>
    </span>
  )
}

export default SaveStatusBadge
