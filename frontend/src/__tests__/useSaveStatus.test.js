/**
 * Unit tests for useSaveStatus + SaveStatusBadge (S2-FE5 / US-08 autosave indicator).
 *
 * The project's frontend suite runs in the Node environment with plain vitest
 * asserts (no @testing-library / jsdom), so these tests exercise the exported
 * constants and the component's status-derived markup.
 */
import { describe, it, expect } from 'vitest'
import { SAVE_STATUS } from '../components/canvas/useSaveStatus'
import { SaveStatusBadge } from '../components/canvas/SaveStatusBadge'

describe('SaveStatusBadge status labels (S2-FE5 / US-08)', () => {
  function labelOf(status) {
    const el = SaveStatusBadge({ status })
    // el.props.children is [dotSpan, labelSpan]; label text is labelSpan.props.children
    const labelSpan = el.props.children[1]
    return labelSpan.props.children
  }

  it('maps Saving state to the "Saving..." label', () => {
    expect(labelOf(SAVE_STATUS.SAVING)).toBe('Saving...')
  })

  it('maps Saved state to the "Saved" label', () => {
    expect(labelOf(SAVE_STATUS.SAVED)).toBe('Saved')
  })

  it('maps Error state to the retry label', () => {
    expect(labelOf(SAVE_STATUS.ERROR)).toBe('Save failed (Retry)')
  })
})

describe('SaveStatusBadge wiring', () => {
  it('error state sets role="button", tabIndex 0, and an onRetry handler', () => {
    const onRetry = () => {}
    const el = SaveStatusBadge({ status: SAVE_STATUS.ERROR, onRetry })
    expect(el.props.role).toBe('button')
    expect(el.props.tabIndex).toBe(0)
    expect(typeof el.props.onClick).toBe('function')
    expect(typeof el.props.onKeyDown).toBe('function')
    expect(el.props.title).toBe('Click to retry saving')
  })

  it('saved state does not set a button role', () => {
    const el = SaveStatusBadge({ status: SAVE_STATUS.SAVED })
    expect(el.props.role).toBe('status')
    expect(el.props.tabIndex).toBe(-1)
  })
})
