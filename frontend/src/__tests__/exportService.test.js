/**
 * Tests for exportService.js — pure helper functions for the map export dialog.
 *
 * Covers:
 *   - File name slugification (sanitizeFileName) and buildExportFileName
 *   - Page dimension resolution + landscape/portrait transform
 *   - Contain-fit image composition math for jsPDF page layout
 *   - Background resolution rules (transparent PNG vs PDF page background)
 */
import { describe, it, expect } from 'vitest'
import {
  resolvePageDimensions,
  computeContainFit,
  sanitizeFileName,
  resolveRenderBackground,
  resolveScaleMultiplier,
  buildExportFileName,
} from '../services/exportService'

describe('resolvePageDimensions', () => {
  it('returns A4 portrait dimensions by default', () => {
    const { width, height } = resolvePageDimensions('A4', 'portrait')
    expect(width).toBeCloseTo(595.28, 1)
    expect(height).toBeCloseTo(841.89, 1)
  })

  it('returns A4 landscape dimensions when orientation flips', () => {
    const { width, height } = resolvePageDimensions('A4', 'landscape')
    expect(width).toBeCloseTo(841.89, 1)
    expect(height).toBeCloseTo(595.28, 1)
  })

  it('returns Letter landscape dimensions', () => {
    const { width, height } = resolvePageDimensions('Letter', 'landscape')
    expect(width).toBe(792)
    expect(height).toBe(612)
  })

  it('falls back to A4 for unknown page size', () => {
    const { width, height } = resolvePageDimensions('B5', 'portrait')
    expect(width).toBeCloseTo(595.28, 1)
    expect(height).toBeCloseTo(841.89, 1)
  })
})

describe('computeContainFit', () => {
  it('maps wide image into box preserving aspect ratio (contain)', () => {
    const fit = computeContainFit(2000, 1000, 547, 588)
    expect(fit.scale).toBeCloseTo(0.2735, 3)
    expect(fit.width).toBe(547)
    expect(fit.height).toBeLessThanOrEqual(588)
    expect(fit.x).toBe(0)
    expect(fit.y).toBeCloseTo(Math.floor((588 - fit.height) / 2), 0)
  })

  it('maps tall image into box preserving aspect ratio (contain)', () => {
    const fit = computeContainFit(1000, 2000, 500, 500)
    expect(fit.scale).toBe(0.25)
    expect(fit.height).toBe(500)
    expect(fit.width).toBe(250)
    expect(fit.y).toBe(0)
    expect(fit.x).toBeCloseTo(Math.floor((500 - fit.width) / 2), 0)
  })

  it('returns box full-size when image dims are invalid', () => {
    expect(computeContainFit(0, 0, 300, 200)).toEqual({
      width: 300,
      height: 200,
      x: 0,
      y: 0,
      scale: 1,
    })
  })
})

describe('sanitizeFileName', () => {
  it('slugifies special characters and spaces into safe name', () => {
    expect(sanitizeFileName('My Mind Map! 2026')).toBe('my_mind_map_2026')
  })

  it('keeps hyphens and underscores intact', () => {
    expect(sanitizeFileName('Quarterly_Plan-v2')).toBe('quarterly_plan-v2')
  })

  it('falls back when the title is empty or all-stripped', () => {
    expect(sanitizeFileName('', 'report')).toBe('report')
    expect(sanitizeFileName('!!!', 'report')).toBe('report')
  })
})

describe('buildExportFileName', () => {
  it('uses the slugged title and the requested format extension', () => {
    expect(buildExportFileName('My Map', 'pdf')).toBe('my_map_mindmap.pdf')
    expect(buildExportFileName('My Map', 'png')).toBe('my_map_mindmap.png')
  })
})

describe('resolveRenderBackground', () => {
  it('honors transparent background only for PNG', () => {
    expect(resolveRenderBackground('transparent', 'png')).toBe('transparent')
    expect(resolveRenderBackground('transparent', 'pdf')).toBe('#FFFFFF')
  })

  it('maps dark and light backgrounds to their hex values', () => {
    expect(resolveRenderBackground('dark', 'pdf')).toBe('#181D1A')
    expect(resolveRenderBackground('dark', 'png')).toBe('#181D1A')
    expect(resolveRenderBackground('light', 'pdf')).toBe('#FFFFFF')
  })
})

describe('resolveScaleMultiplier', () => {
  it('maps 1x and 2x choices to device pixel ratios', () => {
    expect(resolveScaleMultiplier('2x')).toBe(2)
    expect(resolveScaleMultiplier('1x')).toBe(1)
    expect(resolveScaleMultiplier('bogus')).toBe(1)
  })
})
