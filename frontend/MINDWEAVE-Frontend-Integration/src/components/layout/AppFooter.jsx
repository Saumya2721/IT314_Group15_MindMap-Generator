import React from 'react';

export function AppFooter() {
  return (
    <footer
      style={{
        width: '100%',
        backgroundColor: 'var(--surface-container-low)',
        borderTop: '1px solid var(--surface-container-high)',
        padding: '24px 0',
        marginTop: '40px',
      }}
    >
      <div
        style={{
          maxWidth: '1280px',
          margin: '0 auto',
          padding: '0 24px',
          display: 'flex',
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '16px',
          fontSize: '12px',
          color: 'var(--outline)',
        }}
      >
        <div>© 2024 MINDWEAVE. An intellectual sanctuary for creative ideation.</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
          <a
            href="#archive"
            onClick={(e) => e.preventDefault()}
            style={{ color: 'var(--outline)', textDecoration: 'none' }}
            onMouseOver={(e) => (e.target.style.color = 'var(--on-surface)')}
            onMouseOut={(e) => (e.target.style.color = 'var(--outline)')}
          >
            Archive
          </a>
          <a
            href="#systems"
            onClick={(e) => e.preventDefault()}
            style={{ color: 'var(--outline)', textDecoration: 'none' }}
            onMouseOver={(e) => (e.target.style.color = 'var(--on-surface)')}
            onMouseOut={(e) => (e.target.style.color = 'var(--outline)')}
          >
            Systems
          </a>
          <span style={{ fontSize: '11px', color: 'var(--outline)' }}>v2.4 Atelier</span>
        </div>
      </div>
    </footer>
  );
}
