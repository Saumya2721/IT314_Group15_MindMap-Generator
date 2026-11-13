import { useCallback, useEffect, useRef, useState } from 'react'

/** Autosave state machine for the editor header (S2-FE5 / US-08). */
export const SAVE_STATUS = {
  SAVING: 'saving',
  SAVED: 'saved',
  ERROR: 'error',
}

export const SAVE_AUTOSAVE_DEBOUNCE_MS = 300

/**
 * useSaveStatus — manages the autosave indicator states for the editor
 * header: 'Saving...', 'Saved', or 'Save failed (Retry)'.
 *
 * - scheduleSave() triggers a debounced autosave (default 300ms).
 * - flushSave() cancels any pending debounce and runs the save immediately.
 * - retry() re-runs the last save immediately after a failure.
 *
 * The save is invoked through a ref so callers can pass a fresh callback and
 * always see the latest state (title / viewport) without re-creating the timer.
 */
export function useSaveStatus(saveFn, { debounceMs = SAVE_AUTOSAVE_DEBOUNCE_MS } = {}) {
  const [status, setStatus] = useState(SAVE_STATUS.SAVED)
  const [error, setError] = useState(null)
  const pendingTimerRef = useRef(null)
  const saveFnRef = useRef(saveFn)

  useEffect(() => {
    saveFnRef.current = saveFn
  }, [saveFn])

  const clearPendingTimer = useCallback(() => {
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current)
      pendingTimerRef.current = null
    }
  }, [])

  const runSave = useCallback(async () => {
    clearPendingTimer()
    setStatus(SAVE_STATUS.SAVING)
    try {
      const result = await saveFnRef.current()
      setStatus(SAVE_STATUS.SAVED)
      setError(null)
      return { ok: true, result }
    } catch (err) {
      setStatus(SAVE_STATUS.ERROR)
      setError(err)
      return { ok: false, err }
    }
  }, [clearPendingTimer])

  const scheduleSave = useCallback(() => {
    clearPendingTimer()
    setStatus(SAVE_STATUS.SAVING)
    pendingTimerRef.current = setTimeout(() => {
      pendingTimerRef.current = null
      void runSave()
    }, debounceMs)
  }, [clearPendingTimer, debounceMs, runSave])

  const flushSave = useCallback(() => {
    clearPendingTimer()
    return runSave()
  }, [clearPendingTimer, runSave])

  const retry = useCallback(() => {
    clearPendingTimer()
    return runSave()
  }, [clearPendingTimer, runSave])

  useEffect(
    () => () => {
      clearPendingTimer()
    },
    [clearPendingTimer],
  )

  return { status, error, scheduleSave, flushSave, retry, runSave }
}

export default useSaveStatus
