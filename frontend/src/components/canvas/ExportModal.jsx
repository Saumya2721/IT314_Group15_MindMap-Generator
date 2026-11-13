import React from 'react'
import { Button } from '../common/Button'
import './ExportModal.css'

export const EXPORT_FORMATS = Object.freeze(['pdf', 'png'])
export const EXPORT_SCOPES = Object.freeze(['full', 'viewport'])
export const EXPORT_SCALES = Object.freeze(['1x', '2x'])
export const EXPORT_BACKGROUNDS = Object.freeze([
  { value: 'light', label: 'Light', description: 'White page background' },
  { value: 'dark', label: 'Dark', description: 'Warm dark background' },
  { value: 'transparent', label: 'Transparent', description: 'Only for PNG export' },
])

function OptionCard({ label, hint, icon, selected, onSelect, disabled }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={`em-option-card ${selected ? 'em-option-card--selected' : ''}`}
      onClick={() => onSelect && onSelect()}
      disabled={disabled}
    >
      <span className="em-option-icon">{icon}</span>
      <span className="em-option-label">{label}</span>
      {hint ? <span className="em-option-hint">{hint}</span> : null}
    </button>
  )
}

function SegmentedControl({ options, value, onChange, disabled }) {
  return (
    <div className="em-segmented">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`em-segmented-btn ${value === option.value ? 'em-segmented-btn--active' : ''}`}
          onClick={() => onChange && onChange(option.value)}
          disabled={disabled}
          aria-pressed={value === option.value}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function ExportModal({
  mapTitle,
  onClose,
  onExport,
  isExporting = false,
  progress = 0,
  errorMessage = '',
}) {
  const [format, setFormat] = React.useState('pdf')
  const [pageSize, setPageSize] = React.useState('A4')
  const [orientation, setOrientation] = React.useState('portrait')
  const [scale, setScale] = React.useState('1x')
  const [background, setBackground] = React.useState('light')
  const [scope, setScope] = React.useState('full')

  const backgroundOptions = EXPORT_BACKGROUNDS.filter(
    (bg) => bg.value !== 'transparent' || format === 'png',
  )

  const handleFormatChange = (nextFormat) => {
    setFormat(nextFormat)
    if (nextFormat === 'png') {
      setBackground((current) => (current === 'transparent' ? 'light' : current))
    }
  }

  const handleBackgroundChange = (nextBackground) => {
    setBackground(nextBackground)
    if (nextBackground === 'transparent' && format === 'pdf') {
      // PDFs require an opaque page background; coerce transparently.
      setBackground('light')
      return
    }
  }

  const handleExportClick = () => {
    if (isExporting) return
    onExport({
      format,
      pageSize,
      orientation,
      scale,
      background,
      scope,
    })
  }

  return (
    <div className="em-backdrop" onClick={() => !isExporting && onClose()}>
      <div
        className="em-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Export mind map"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="em-header">
          <div>
            <h4 className="em-title">Export Mind Map</h4>
            {mapTitle ? <p className="em-subtitle">{mapTitle}</p> : null}
          </div>
          <button
            type="button"
            className="em-close"
            onClick={onClose}
            disabled={isExporting}
            aria-label="Close export dialog"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              close
            </span>
          </button>
        </div>

        <div className="em-body">
          {/* Format */}
          <div className="em-section">
            <span className="em-section-label">Format</span>
            <div className="em-option-grid">
              <OptionCard
                label="PDF"
                hint="Vector document for printing"
                icon={<span className="material-symbols-outlined">description</span>}
                selected={format === 'pdf'}
                onSelect={() => handleFormatChange('pdf')}
                disabled={isExporting}
              />
              <OptionCard
                label="PNG"
                hint="Raster image for sharing"
                icon={<span className="material-symbols-outlined">image</span>}
                selected={format === 'png'}
                onSelect={() => handleFormatChange('png')}
                disabled={isExporting}
              />
            </div>
          </div>

          {/* Scope */}
          <div className="em-section">
            <span className="em-section-label">Content</span>
            <SegmentedControl
              options={[
                { value: 'full', label: 'Full map' },
                { value: 'viewport', label: 'Visible viewport' },
              ]}
              value={scope}
              onChange={setScope}
              disabled={isExporting}
            />
            <p className="em-hint">Export the complete mind map or just what is on screen.</p>
          </div>

          {format === 'pdf' && (
            <div className="em-section">
              <span className="em-section-label">Page Size</span>
              <SegmentedControl
                options={[
                  { value: 'A4', label: 'A4' },
                  { value: 'Letter', label: 'Letter' },
                ]}
                value={pageSize}
                onChange={setPageSize}
                disabled={isExporting}
              />
              <span className="em-section-label" style={{ marginTop: 'var(--space-md)' }}>
                Orientation
              </span>
              <SegmentedControl
                options={[
                  { value: 'portrait', label: 'Portrait' },
                  { value: 'landscape', label: 'Landscape' },
                ]}
                value={orientation}
                onChange={setOrientation}
                disabled={isExporting}
              />
            </div>
          )}

          {/* Scale & Background */}
          <div className="em-section">
            <span className="em-section-label">Resolution</span>
            <SegmentedControl
              options={EXPORT_SCALES.map((value) => ({ value, label: `${value} scale` }))}
              value={scale}
              onChange={setScale}
              disabled={isExporting}
            />
            <span className="em-section-label" style={{ marginTop: 'var(--space-md)' }}>
              Background
            </span>
            <SegmentedControl
              options={backgroundOptions.map((bg) => ({
                value: bg.value,
                label: bg.label,
              }))}
              value={background}
              onChange={handleBackgroundChange}
              disabled={isExporting}
            />
            <p className="em-hint">
              Transparent is available for PNG exports; PDF exports automatically use a white page.
            </p>
          </div>
        </div>

        {isExporting ? (
          <div className="em-progress-area">
            <div
              className="em-progress-track"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress)}
              aria-label="Export progress"
            >
              <div className="em-progress-fill" style={{ width: `${Math.round(progress)}%` }} />
            </div>
            <span className="em-progress-text">{Math.round(progress)}%</span>
          </div>
        ) : null}

        {errorMessage ? (
          <div className="em-error" role="alert">
            <span>{errorMessage}</span>
          </div>
        ) : null}

        <div className="em-footer">
          <Button
            variant="secondary"
            className="em-cancel"
            onClick={onClose}
            disabled={isExporting}
          >
            Cancel
          </Button>
          <Button className="em-confirm" onClick={handleExportClick} disabled={isExporting}>
            {isExporting ? 'Exporting…' : 'Export'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default ExportModal
