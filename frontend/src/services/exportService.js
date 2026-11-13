/**
 * Export Service for MindWeave
 * Renders the current mind map canvas to a downloadable PDF or PNG file
 * using the browser DOM (html2canvas) and jsPDF.
 *
 * Two export scopes are supported:
 * - `viewport` — captures exactly what the user sees right now.
 * - `full` — refits the React Flow viewport to include EVERY node, captures,
 *   then restores the user's previous viewport.
 *
 * The functions `resolvePageDimensions`, `computeContainFit`, `sanitizeFileName`
 * and `resolveRenderBackground` are exported pure helpers used by unit tests.
 */

export const PAGE_SIZES = Object.freeze({
  A4: { label: 'A4', width: 595.28, height: 841.89 },
  Letter: { label: 'Letter', width: 612, height: 792 },
})

const DARK_BACKGROUND = '#181D1A'
const LIGHT_BACKGROUND = '#FFFFFF'
export const TRANSPARENT_BACKGROUND = 'transparent'

/**
 * Resolve the page dimensions (in points, 72dpi) for a page size + orientation.
 * Pure helper — unit tested.
 */
export function resolvePageDimensions(pageSize = 'A4', orientation = 'portrait') {
  const base = PAGE_SIZES[pageSize] || PAGE_SIZES.A4
  if (orientation === 'landscape') {
    return { width: base.height, height: base.width }
  }
  return { width: base.width, height: base.height }
}

/**
 * Resolve the background color to paint behind the exported raster.
 * Pure helper — unit tested.
 * Transparent is honored only for PNG; PDF exports always paint a page
 * background so the document remains legible when printed.
 */
export function resolveRenderBackground(background = 'light', format = 'pdf') {
  switch (background) {
    case 'dark':
      return DARK_BACKGROUND
    case 'transparent':
      return format === 'png' ? TRANSPARENT_BACKGROUND : LIGHT_BACKGROUND
    case 'light':
    default:
      return LIGHT_BACKGROUND
  }
}

/**
 * Compute a "contain" fit: scale image inside a box preserving aspect ratio.
 * Returns { width, height, x, y, scale }. Pure helper — unit tested.
 */
export function computeContainFit(imageWidth, imageHeight, boxWidth, boxHeight) {
  if (
    !Number.isFinite(imageWidth) ||
    !Number.isFinite(imageHeight) ||
    imageWidth <= 0 ||
    imageHeight <= 0
  ) {
    return { width: boxWidth, height: boxHeight, x: 0, y: 0, scale: 1 }
  }
  const scale = Math.min(boxWidth / imageWidth, boxHeight / imageHeight)
  const width = Math.floor(imageWidth * scale)
  const height = Math.floor(imageHeight * scale)
  return {
    width,
    height,
    x: Math.floor((boxWidth - width) / 2),
    y: Math.floor((boxHeight - height) / 2),
    scale,
  }
}

/**
 * Slugify a map title into a safe file name component.
 * Pure helper — unit tested.
 */
export function sanitizeFileName(title, fallback = 'mindmap') {
  const cleaned = String(title || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\s_-]/g, '')
    .replace(/[\s_]+/g, '_')
    .replace(/-+/g, '-')
    .replace(/^[_-]+|[_-]+$/g, '')
  return cleaned || fallback
}

/** Map raw UI scale choice to html2canvas `scale` device multiplier. */
export function resolveScaleMultiplier(scale = '1x') {
  return scale === '2x' ? 2 : 1
}

const STAGE_LABELS = {
  10: 'Preparing export…',
  35: 'Framing full map layout…',
  55: 'Rasterizing canvas…',
  75: 'Composing document…',
  90: 'Saving file…',
  100: 'Export complete',
}

/** Generate the exported file name. */
export function buildExportFileName(title, format = 'png') {
  return `${sanitizeFileName(title)}_mindmap.${format}`
}

/**
 * Export the mind map canvas to a file and trigger a download.
 *
 * @param {object} args
 * @param {string} args.mapTitle      - Title used for the exported file name
 * @param {object} args.options       - { format, pageSize, orientation, scale, background, scope }
 * @param {function} args.onProgress  - Called with 0-100 progress values
 * @param {function} args.onStage     - Optional stage label callback
 * @param {{ fitAllNodes: Function, getViewport: Function }} args.flowApi - React Flow imperative API
 * @returns {Promise<{ fileName: string, format: string }>}
 */
export async function exportMindMap({ mapTitle, options = {}, onProgress, onStage, flowApi }) {
  const format = options.format === 'png' ? 'png' : 'pdf'
  const background = options.background || 'light'
  const scale = options.scale || '1x'
  const pageSize = options.pageSize === 'Letter' ? 'Letter' : 'A4'
  const orientation = options.orientation === 'landscape' ? 'landscape' : 'portrait'
  const scope = options.scope === 'full' ? 'full' : 'viewport'

  const report = (value) => {
    const clamped = Math.max(0, Math.min(100, Math.round(value)))
    const keys = Object.keys(STAGE_LABELS)
      .map(Number)
      .filter((k) => k <= clamped)
    const stageKey = keys.length > 0 ? keys[keys.length - 1] : 10
    const stageLabel = STAGE_LABELS[clamped] || STAGE_LABELS[stageKey]
    if (typeof onProgress === 'function') onProgress(clamped)
    if (typeof onStage === 'function') onStage(stageLabel)
  }

  const container = document.getElementById('mindmap-canvas-export')
  if (!container) {
    throw new Error('Export target not found. Switch to Canvas view and try again.')
  }

  const previousViewport = typeof flowApi?.getViewport === 'function' ? flowApi.getViewport() : null

  try {
    report(10)

    // 1) If full-map export requested, refit the viewport to include all nodes
    if (scope === 'full' && typeof flowApi?.fitAllNodes === 'function') {
      report(35)
      await flowApi.fitAllNodes()
      // Let React Flow + edges settle before rasterizing
      await new Promise((resolve) => setTimeout(resolve, 120))
    }

    // 2) Rasterize the canvas DOM
    report(55)
    const { default: html2canvas } = await import('html2canvas')
    const renderBackground = resolveRenderBackground(background, format)
    const canvas = await html2canvas(container, {
      backgroundColor: renderBackground === TRANSPARENT_BACKGROUND ? null : renderBackground,
      scale: resolveScaleMultiplier(scale),
      useCORS: true,
      logging: false,
      ignoreElements: (element) =>
        element.classList?.contains('react-flow__controls') ||
        element.classList?.contains('react-flow__minimap') ||
        element.classList?.contains('node-inspector-flyout') ||
        element.classList?.contains('canvas-toast-alert'),
    })

    report(75)

    // 3) Compose output document
    const fileName = buildExportFileName(mapTitle, format)

    if (format === 'png') {
      await downloadCanvasAsPng(canvas, fileName)
    } else {
      const { default: jsPDF } = await import('jspdf')
      composePdf(jsPDF, canvas, { pageSize, orientation, background: renderBackground, fileName })
    }

    report(100)
    return { fileName, format }
  } catch (err) {
    const message = err?.message || 'Export failed. Please try again.'
    throw new Error(message, { cause: err })
  } finally {
    // 4) Always restore the user's viewport, even on failure
    if (previousViewport && typeof flowApi?.setViewport === 'function') {
      try {
        await flowApi.setViewport(previousViewport)
      } catch {
        // Restoring the viewport is best-effort; export result still stands
      }
    }
  }
}

function downloadCanvasAsPng(canvas, fileName) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('Failed to encode the PNG image.'))
        return
      }
      triggerBlobDownload(blob, fileName)
      resolve(fileName)
    }, 'image/png')
  })
}

function composePdf(jsPDF, canvas, { pageSize, orientation, background, fileName }) {
  const { width, height } = resolvePageDimensions(pageSize, orientation)
  const margin = 24 // 72dpi / 3 = 24pt
  const box = { width: width - margin * 2, height: height - margin * 2 }
  const fit = computeContainFit(canvas.width, canvas.height, box.width, box.height)

  const orientationName = orientation === 'landscape' ? 'landscape' : 'portrait'
  const doc = new jsPDF({
    orientation: orientationName,
    unit: 'pt',
    format: pageSize === 'Letter' ? 'letter' : 'a4',
    compress: true,
  })

  // Paint page background (opaque for print fidelity)
  const rgb = hexToRgb(background || LIGHT_BACKGROUND)
  doc.setFillColor(rgb.r, rgb.g, rgb.b)
  doc.rect(0, 0, width, height, 'F')

  const dataUrl = canvas.toDataURL('image/png')
  doc.addImage(
    dataUrl,
    'PNG',
    margin + fit.x,
    margin + fit.y,
    fit.width,
    fit.height,
    undefined,
    'FAST',
  )
  doc.save(fileName)
}

function hexToRgb(hex) {
  const value = String(hex || LIGHT_BACKGROUND).replace('#', '')
  const normalized =
    value.length === 3
      ? value
          .split('')
          .map((c) => c + c)
          .join('')
      : value.padEnd(6, 'f')
  const r = parseInt(normalized.slice(0, 2), 16) || 255
  const g = parseInt(normalized.slice(2, 4), 16) || 255
  const b = parseInt(normalized.slice(4, 6), 16) || 255
  return { r, g, b }
}

function triggerBlobDownload(blob, fileName) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
