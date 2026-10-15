import React from 'react';

export function MindMapNode({ label, type = 'default' }) {
  return (
    <div
      style={{
        padding: '12px 16px',
        backgroundColor: 'var(--card-surface)',
        borderRadius: 'var(--radius-default)',
        border: '1px solid var(--structural-border)',
        boxShadow: 'var(--shadow-paper)',
      }}
    >
      <span style={{ fontFamily: 'var(--font-headline)', fontWeight: 500, fontSize: '15px' }}>
        {label}
      </span>
    </div>
  );
}
