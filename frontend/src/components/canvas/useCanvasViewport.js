import { useEffect, useMemo, useRef, useState } from 'react'

/**
 * Default editor viewport: centered at (0, 0) with a comfortable zoom.
 * Matches the React Flow default viewport used by MindMapCanvas.
 */
export const DEFAULT_VIEWPORT = { x: 0, y: 0, zoom: 1 }

/**
 * useCanvasViewport — capture and restore the React Flow viewport (pan + zoom)
 * on map reopen (S2-FE5 / US-08 acceptance criterion #2).
 *
 * - `initialViewport` (from persisted mind_maps.view_x/view_y/view_zoom) seeds the
 *   state on first mount.
 * - `flowApiRef` exposes the React Flow instance; the hook polls it on mount and
 *   on every `nodes`/`edges` change so the user can pan/zoom and the latest
 *   viewport is captured.
 * - `onViewportChange` is called on every capture (debounced 500ms) so the
 *   parent can persist it to the server.
 */
export function useCanvasViewport({ initialViewport, flowApiRef, onViewportChange }) {
  const [viewport, setViewport] = useState(initialViewport)
  const captureTimerRef = useRef(null)
  const onViewportChangeRef = useRef(onViewportChange)

  useEffect(() => {
    onViewportChangeRef.current = onViewportChange
  }, [onViewportChange])

  const capture = useMemo(
    () => () => {
      const api = flowApiRef?.current
      if (!api) return
      try {
        const vp = api.getViewport()
        if (!vp) return
        const next = { x: vp.x, y: vp.y, zoom: vp.zoom }
        setViewport(next)
        if (onViewportChangeRef.current) {
          if (captureTimerRef.current) clearTimeout(captureTimerRef.current)
          captureTimerRef.current = setTimeout(() => {
            captureTimerRef.current = null
            onViewportChangeRef.current(next)
          }, 500)
        }
      } catch {
        // getViewport may throw before the Flow instance is ready; ignore.
      }
    },
    [flowApiRef],
  )

  useEffect(() => {
    const api = flowApiRef?.current
    if (!api) return
    // Restore the persisted viewport on mount / graph change.
    try {
      api.fitView?.({ padding: 0.25 })
    } catch {
      // ignore
    }
    const id = setTimeout(() => {
      try {
        api.setViewport?.(initialViewport)
      } catch {
        // ignore
      }
    }, 50)
    return () => clearTimeout(id)
  }, [initialViewport, flowApiRef])

  return { viewport, capture }
}

export default useCanvasViewport
