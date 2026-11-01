import React from 'react'
import './ArchivalStepper.css'

export function ArchivalStepper({ currentStep = 1, progressPercent = 0 }) {
  const steps = [
    {
      id: 1,
      title: 'Reading your input',
      statusTag: progressPercent >= 30 ? '100%' : undefined,
      desc: 'Normalizing raw ideas, text notes, and attached source material.',
    },
    {
      id: 2,
      title: 'Extracting concepts',
      statusTag: progressPercent >= 60 ? 'Done' : undefined,
      desc: 'Identifying domain entities, central themes, and conceptual anchors.',
    },
    {
      id: 3,
      title: 'Structuring graph hierarchy',
      desc: 'Connecting thematic branches and topological relationships...',
      hasProgressBar: true,
    },
    {
      id: 4,
      title: 'Finalizing knowledge map',
      desc: 'Resolving coordinates, link labels, and visual hierarchy...',
    },
  ]

  return (
    <div className="archival-stepper">
      {/* Connecting vertical line */}
      <div className="stepper-vertical-line" />

      {steps.map((step) => {
        const isCompleted = step.id < currentStep || (step.id === 4 && progressPercent >= 100)
        const isActive = step.id === currentStep && progressPercent < 100
        const isPending = step.id > currentStep

        return (
          <div
            key={step.id}
            className={`stepper-step ${isPending ? 'pending' : ''} ${isActive ? 'active' : ''}`}
          >
            {/* Step Icon */}
            <div className="step-indicator-wrapper">
              {isCompleted ? (
                <div className="step-indicator-check">
                  <span
                    className="material-symbols-outlined"
                    style={{ fontSize: '13px', fontWeight: 'bold' }}
                  >
                    check
                  </span>
                </div>
              ) : isActive ? (
                <div className="step-indicator-active">
                  <span className="pulse-ring" />
                  <span className="pulse-dot" />
                </div>
              ) : (
                <div className="step-indicator-pending" />
              )}
            </div>

            {/* Step Details */}
            <div className="step-details">
              <div className="step-title-row">
                <span className="step-title">{step.title}</span>
                {step.statusTag && isCompleted && (
                  <span className="step-status-tag">{step.statusTag}</span>
                )}
                {isActive && (
                  <span className="step-status-tag live">
                    <span className="material-symbols-outlined step-spin-icon">sync</span>
                    <span>Processing</span>
                  </span>
                )}
              </div>

              <p className="step-desc">{step.desc}</p>

              {/* Progress bar active during generation */}
              {step.hasProgressBar && (isActive || isCompleted) && (
                <div className="step-progress-wrapper">
                  <div className="step-progress-track">
                    <div
                      className="step-progress-fill"
                      style={{ width: `${Math.min(progressPercent, 100)}%` }}
                    />
                  </div>
                  <span className="step-progress-label">{Math.min(progressPercent, 100)}%</span>
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
