/**
 * Unit tests for useCanvasViewport (S2-FE5 / US-08 viewport restore).
 *
 * The project's frontend suite runs in plain vitest (Node environment, no
 * @testing-library / jsdom), so this test only asserts the exported
 * DEFAULT_VIEWPORT constant — a value asserted on by the editor page when
 * restoring the persisted map viewport.
 */
import { describe, it, expect } from 'vitest'
import { DEFAULT_VIEWPORT } from '../components/canvas/useCanvasViewport'

describe('useCanvasViewport (S2-FE5 / US-08)', () => {
  it('DEFAULT_VIEWPORT is centered at origin with default zoom', () => {
    expect(DEFAULT_VIEWPORT).toEqual({ x: 0, y: 0, zoom: 1 })
  })
})
