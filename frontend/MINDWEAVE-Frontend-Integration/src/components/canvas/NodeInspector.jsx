import React from 'react';
import './NodeInspector.css';

export function NodeInspector({
  selectedNode,
  onClose,
  onUpdateTitle,
  onUpdateDescription,
  onUpdateTint,
  onAddChildNode,
  onAiExpand,
}) {
  if (!selectedNode) return null;

  // Available tints from the Stitch mockup
  const tints = [
    { label: 'Terracotta', color: '#C96B4B' },
    { label: 'Dark Teal', color: '#155E63' },
    { label: 'Olive', color: '#899B72' },
    { label: 'Carbon Ink', color: '#202522' },
  ];

  const currentTint = selectedNode.tint || '#155E63';

  return (
    <div className="node-inspector-flyout">
      {/* Header */}
      <div className="inspector-header">
        <div className="inspector-header-left">
          <span className="dot" style={{ backgroundColor: currentTint }} />
          <span className="inspector-node-title">{selectedNode.title || 'Selected Node'}</span>
        </div>
        <button
          type="button"
          className="btn-close-inspector"
          onClick={onClose}
          aria-label="Close inspector"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            close
          </span>
        </button>
      </div>

      {/* Editable Node Title */}
      <div className="inspector-field">
        <label className="inspector-field-label">Node Title</label>
        <input
          type="text"
          className="inspector-input"
          value={selectedNode.title || ''}
          onChange={(e) => onUpdateTitle && onUpdateTitle(selectedNode.id, e.target.value)}
        />
      </div>

      {/* Editable Description */}
      <div className="inspector-field">
        <label className="inspector-field-label">Description / Subtitle</label>
        <input
          type="text"
          className="inspector-input"
          value={selectedNode.subtitle || ''}
          onChange={(e) =>
            onUpdateDescription && onUpdateDescription(selectedNode.id, e.target.value)
          }
          placeholder="Add clarifying note..."
        />
      </div>

      {/* Taxonomy Tint Palette */}
      <div className="inspector-tint-section">
        <span className="inspector-field-label">Taxonomy Tint</span>
        <div className="tint-dots-row">
          {tints.map((t) => {
            const isSelected = currentTint.toLowerCase() === t.color.toLowerCase();
            return (
              <button
                key={t.color}
                type="button"
                className={`btn-tint-dot ${isSelected ? 'active' : ''}`}
                style={{ backgroundColor: t.color }}
                onClick={() => onUpdateTint && onUpdateTint(selectedNode.id, t.color)}
                title={t.label}
              >
                {isSelected && (
                  <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                    check
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Action Buttons */}
      <div className="inspector-actions-row">
        <button
          type="button"
          className="btn-inspector-action"
          onClick={() => onAddChildNode && onAddChildNode(selectedNode.id)}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
            add_box
          </span>
          <span>Child Node</span>
        </button>

        <button
          type="button"
          className="btn-inspector-action"
          onClick={() => onAiExpand && onAiExpand(selectedNode.id)}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px', color: 'var(--primary)' }}>
            auto_awesome
          </span>
          <span>AI Expand</span>
        </button>
      </div>
    </div>
  );
}
