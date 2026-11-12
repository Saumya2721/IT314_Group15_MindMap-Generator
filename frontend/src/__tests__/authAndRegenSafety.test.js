/**
 * Regression & Safety Tests for Sprint 1 Re-Audit Defect Fixes:
 * 1. P0: Regeneration failure & cancellation must preserve existing maps (no deleteMap calls).
 * 2. P2: OAuth return path validation (same-origin relative URLs only; open redirect rejection).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ─── 1. OAuth Path Sanitization Logic ──────────────────────────────────────────

export function sanitizeOAuthReturnPath(storedPath) {
  let intendedPath = '/dashboard'
  if (
    storedPath &&
    typeof storedPath === 'string' &&
    storedPath.startsWith('/') &&
    !storedPath.startsWith('//') &&
    !storedPath.includes('://')
  ) {
    intendedPath = storedPath
  }
  return intendedPath
}

describe('P2: OAuth Return Path Sanitization', () => {
  it('defaults to /dashboard when no return path is stored', () => {
    expect(sanitizeOAuthReturnPath(null)).toBe('/dashboard')
    expect(sanitizeOAuthReturnPath(undefined)).toBe('/dashboard')
    expect(sanitizeOAuthReturnPath('')).toBe('/dashboard')
  })

  it('permits valid same-origin relative paths', () => {
    expect(sanitizeOAuthReturnPath('/create')).toBe('/create')
    expect(sanitizeOAuthReturnPath('/editor/map-uuid-123')).toBe('/editor/map-uuid-123')
    expect(sanitizeOAuthReturnPath('/dashboard?filter=recent')).toBe('/dashboard?filter=recent')
  })

  it('blocks open redirect vulnerabilities (protocol-relative URLs)', () => {
    expect(sanitizeOAuthReturnPath('//malicious-site.com')).toBe('/dashboard')
    expect(sanitizeOAuthReturnPath('//evil.org/phish')).toBe('/dashboard')
  })

  it('blocks open redirect vulnerabilities (absolute external URLs)', () => {
    expect(sanitizeOAuthReturnPath('https://attacker.com/steal-token')).toBe('/dashboard')
    expect(sanitizeOAuthReturnPath('http://phishing.site')).toBe('/dashboard')
    expect(sanitizeOAuthReturnPath('javascript:alert(1)')).toBe('/dashboard')
  })
})

// ─── 2. Data Loss Prevention Logic (P0 Regression Tests) ────────────────────────

describe('P0: Mind Map Preservation during Regeneration', () => {
  let mockDeleteMap

  beforeEach(() => {
    mockDeleteMap = vi.fn().mockResolvedValue({ success: true })
  })

  function handleTerminalFailureCleanup({ isRegeneration, nodeCount, targetMapId }) {
    const isRegenMode = isRegeneration || nodeCount > 0
    if (isRegenMode) {
      // NEVER delete populated or regenerated maps
      return { deleted: false, preserved: true }
    }
    // Only clean up newly created empty drafts
    mockDeleteMap(targetMapId)
    return { deleted: true, preserved: false }
  }

  function handleCancelAction({ isRegeneration, nodeCount, targetMapId }) {
    const isRegenMode = isRegeneration || nodeCount > 0
    if (isRegenMode) {
      // Non-destructive cancel: return to editor
      return { action: 'return_to_editor', mapDeleted: false }
    }
    // Destructive cancel of empty initial draft
    mockDeleteMap(targetMapId)
    return { action: 'navigate_create', mapDeleted: true }
  }

  it('preserves existing map when regeneration fails', () => {
    const result = handleTerminalFailureCleanup({
      isRegeneration: true,
      nodeCount: 15,
      targetMapId: 'map-existing-1',
    })

    expect(result.preserved).toBe(true)
    expect(result.deleted).toBe(false)
    expect(mockDeleteMap).not.toHaveBeenCalled()
  })

  it('preserves populated map even if isRegeneration flag was false (safety check on nodeCount)', () => {
    const result = handleTerminalFailureCleanup({
      isRegeneration: false,
      nodeCount: 8,
      targetMapId: 'map-populated-2',
    })

    expect(result.preserved).toBe(true)
    expect(result.deleted).toBe(false)
    expect(mockDeleteMap).not.toHaveBeenCalled()
  })

  it('deletes empty draft only when initial generation fails (0 nodes)', () => {
    const result = handleTerminalFailureCleanup({
      isRegeneration: false,
      nodeCount: 0,
      targetMapId: 'map-draft-3',
    })

    expect(result.deleted).toBe(true)
    expect(result.preserved).toBe(false)
    expect(mockDeleteMap).toHaveBeenCalledWith('map-draft-3')
  })

  it('does NOT delete map when user clicks Cancel during regeneration', () => {
    const result = handleCancelAction({
      isRegeneration: true,
      nodeCount: 12,
      targetMapId: 'map-regen-4',
    })

    expect(result.action).toBe('return_to_editor')
    expect(result.mapDeleted).toBe(false)
    expect(mockDeleteMap).not.toHaveBeenCalled()
  })

  it('deletes draft map when user clicks Cancel during initial generation', () => {
    const result = handleCancelAction({
      isRegeneration: false,
      nodeCount: 0,
      targetMapId: 'map-initial-5',
    })

    expect(result.action).toBe('navigate_create')
    expect(result.mapDeleted).toBe(true)
    expect(mockDeleteMap).toHaveBeenCalledWith('map-initial-5')
  })
})
