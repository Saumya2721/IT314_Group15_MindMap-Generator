import React from 'react';
import './ArchivalStepper.css';

export function ArchivalStepper({ currentStep = 3, progressPercent = 68 }) {
  const steps = [
    {
      id: 1,
      title: 'Reading your input',
      statusTag: '100%',
      desc: 'Extracted 18 foundational entities and conceptual keywords from source literature.',
    },
    {
      id: 2,
      title: 'Finding related concepts',
      statusTag: 'Synthesized',
      desc: 'Established 3 core thematic branches and 6 secondary categorical sub-branches.',
    },
    {
      id: 3,
      title: 'Organizing the hierarchy',
      desc: 'Structuring cognitive frameworks and topological node relationships...',
      hasProgressBar: true,
    },
    {
      id: 4,
      title: 'Building your mind map',
      desc: 'Calculating coordinate placement, spatial breathing room, and node topology...',
    },
  ];

  return (
    <div className="archival-stepper">
      {/* Connecting vertical line */}
      <div className="stepper-vertical-line" />

      {steps.map((step) => {
        const isCompleted = step.id < currentStep || (step.id === 3 && progressPercent >= 100);
        const isActive = step.id === currentStep && progressPercent < 100;
        const isPending = step.id > currentStep;

        return (
          <div
            key={step.id}
            className={`stepper-step ${isPending ? 'pending' : ''} ${isActive ? 'active' : ''}`}
          >
            {/* Step Icon */}
            <div className="step-indicator-wrapper">
              {isCompleted ? (
                <div className="step-indicator-check">
                  <span className="material-symbols-outlined" style={{ fontSize: '13px', fontWeight: 'bold' }}>
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

            {/* Step Text Info */}
            <div className="step-content">
              <div className="step-title-row">
                <div className="step-title-left">
                  <span className={`step-title ${isActive ? 'active' : ''}`}>
                    {step.title}
                  </span>
                  {isActive && (
                    <span className="material-symbols-outlined step-spin-icon">
                      progress_activity
                    </span>
                  )}
                </div>

                {isCompleted && step.statusTag && (
                  <span className="step-status-tag">{step.statusTag}</span>
                )}

                {isActive && (
                  <span className="step-percentage">{progressPercent}%</span>
                )}
              </div>

              <p className="step-desc">{step.desc}</p>

              {/* Progress Bar for active step */}
              {step.hasProgressBar && isActive && (
                <div className="stepper-progress-track">
                  <div
                    className="stepper-progress-fill"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
