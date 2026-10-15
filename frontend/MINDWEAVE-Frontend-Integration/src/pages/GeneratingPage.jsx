import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation, useParams } from 'react-router-dom';
import { useMindMap } from '../context/MindMapContext';
import { AppHeader } from '../components/layout/AppHeader';
import { AppFooter } from '../components/layout/AppFooter';
import { ArchivalStepper } from '../components/generator/ArchivalStepper';
import { CanvasTopBar } from '../components/canvas/CanvasTopBar';
import { MindMapCanvas } from '../components/canvas/MindMapCanvas';
import './GeneratingPage.css';

export default function GeneratingPage({ initialPhase }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { id } = useParams();
  const { draftMap, maps, createMap } = useMindMap();

  // If opened directly from an editor route or existing map link, start in editor mode
  const isDirectEditor = initialPhase === 'editor' || location.state?.mode === 'editor' || Boolean(id);

  // Phase state: 'generating' or 'editor'
  const [phase, setPhase] = useState(isDirectEditor ? 'editor' : 'generating');

  // Topic and thoughts from route state, context, or existing map
  const existingMap = maps.find((m) => m.id === id);
  const [mapTitle, setMapTitle] = useState(
    existingMap?.title || location.state?.topic || draftMap?.topic || 'Renewable Energy Architecture'
  );
  const thoughts = location.state?.thoughts || draftMap?.thoughts || '';

  // Simulation states
  const [progress, setProgress] = useState(68);
  const [currentStep, setCurrentStep] = useState(3);
  const [isCancelling, setIsCancelling] = useState(false);
  const timerRef = useRef(null);

  // View mode switcher: 'canvas' or 'outline'
  const [viewMode, setViewMode] = useState('canvas');

  // Toast message feedback for actions
  const [toastMessage, setToastMessage] = useState('');
  const toastTimeoutRef = useRef(null);

  const showToast = (msg) => {
    setToastMessage(msg);
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    toastTimeoutRef.current = setTimeout(() => setToastMessage(''), 3000);
  };

  // Generation simulation timer
  useEffect(() => {
    if (phase !== 'generating') return;

    timerRef.current = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 98) {
          clearInterval(timerRef.current);
          return 100;
        }
        const increment = Math.floor(Math.random() * 6) + 4;
        return Math.min(prev + increment, 98);
      });
    }, 550);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [phase]);

  // Transition from 'generating' to 'editor' on the same page
  useEffect(() => {
    if (phase === 'generating' && progress >= 100) {
      setCurrentStep(4);

      const finishTimeout = setTimeout(() => {
        // Register map in mock store
        createMap({
          title: mapTitle,
          rawNotes: thoughts,
        });

        // Transition on the SAME page to the Generated Mind Map Editor
        setPhase('editor');
        showToast('Topological synthesis complete. Workspace ready.');
      }, 750);

      return () => clearTimeout(finishTimeout);
    }
  }, [phase, progress, mapTitle, thoughts, createMap]);

  // Cancel generation handler
  const handleCancel = () => {
    setIsCancelling(true);
    if (timerRef.current) clearInterval(timerRef.current);

    setTimeout(() => {
      navigate('/dashboard');
    }, 600);
  };

  // Canvas action triggers
  const handleShare = () => {
    showToast('Shareable atelier link copied to clipboard.');
  };

  const handleExport = () => {
    showToast('Vector map exported (SVG download ready).');
  };

  const canvasRef = useRef(null);
  const handleAddNode = () => {
    showToast('New concept card added to canvas.');
  };

  // =========================================================
  // RENDER PHASE 2: GENERATED MIND MAP EDITOR (ON SAME PAGE)
  // =========================================================
  if (phase === 'editor') {
    return (
      <div className="generating-editor-view">
        {/* Top Refined Editor Navigation Bar */}
        <CanvasTopBar
          title={mapTitle}
          onTitleChange={setMapTitle}
          onBack={() => navigate('/dashboard')}
          onAddNode={handleAddNode}
          onShare={handleShare}
          onExport={handleExport}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
        />

        {/* Outline Mode or Visual Canvas View */}
        {viewMode === 'outline' ? (
          <div className="outline-view-container">
            <div className="outline-card">
              <h2 className="outline-title font-headline">{mapTitle}</h2>
              <ul className="outline-list">
                <li>
                  <strong>Photovoltaic Systems</strong>
                  <ul>
                    <li>Efficiency Frontiers (Tandem cells)</li>
                    <li>Thermal Degradation &amp; Layering</li>
                  </ul>
                </li>
                <li>
                  <strong>Decentralized Hubs</strong>
                  <ul>
                    <li>Flow Chemistries (Microgrids)</li>
                    <li>Inverter Telemetry (VPPs)</li>
                  </ul>
                </li>
                <li>
                  <strong>Grid Stabilization</strong>
                  <ul>
                    <li>Interconnection &amp; Permitting</li>
                    <li>Sodium-Ion Supply Shift</li>
                  </ul>
                </li>
              </ul>
              <button
                type="button"
                className="btn btn-primary"
                style={{ marginTop: '20px' }}
                onClick={() => setViewMode('canvas')}
              >
                Switch to Canvas View
              </button>
            </div>
          </div>
        ) : (
          <MindMapCanvas
            ref={canvasRef}
            initialTitle={mapTitle}
            initialThoughts={thoughts}
            toastMessage={toastMessage}
            onClearToast={() => setToastMessage('')}
          />
        )}
      </div>
    );
  }

  // =========================================================
  // RENDER PHASE 1: GENERATING STATE
  // =========================================================
  return (
    <div className="generating-layout">
      {/* Top Header */}
      <AppHeader />

      {/* Main Synthesis Console Area */}
      <main className="generating-main">
        <div className="synthesis-console-card">
          {/* Top Decorative Craft Watermark Line */}
          <div className="console-watermark-line" />

          {/* Header Section */}
          <div className="console-header">
            <div className="topic-badge-pill">
              <span className="topic-dot-marker" />
              <span>Topic: {mapTitle}</span>
            </div>
            <h1 className="console-title">Making sense of your ideas</h1>
            <p className="console-subtitle">
              Deconstructing text, synthesizing core concepts, and tracing logical branches with
              deliberate topological clarity.
            </p>
          </div>

          {/* Archival Stepper */}
          <ArchivalStepper currentStep={currentStep} progressPercent={progress} />

          {/* Live Emerging Spatial Preview Canvas */}
          <div className="manifestation-container">
            <div className="manifestation-header-row">
              <span className="manifestation-label">
                <span className="manifestation-label-dot" />
                Cartographic Canvas Manifestation
              </span>
              <span className="manifestation-speed-rate">1.2k nodes/sec</span>
            </div>

            {/* Architectural Viewport */}
            <div className="manifestation-viewport">
              {/* Subtle Drafting Dot Grid Background */}
              <div className="manifestation-grid-bg" />

              {/* Dynamic Vector Connector Paths SVG */}
              <svg className="manifestation-vectors-svg" viewBox="0 0 760 288" fill="none">
                <path
                  d="M 380 144 C 300 144, 240 76, 170 76"
                  stroke="#155e63"
                  strokeWidth="1.5"
                  strokeDasharray="4 3"
                  className="connector-pulse"
                />
                <path
                  d="M 380 144 C 460 144, 520 84, 590 84"
                  stroke="#155e63"
                  strokeWidth="1.5"
                  strokeDasharray="4 3"
                  className="connector-pulse"
                />
                <path
                  d="M 380 144 C 440 144, 490 206, 560 206"
                  stroke="#6f7979"
                  strokeWidth="1.5"
                  strokeDasharray="2 3"
                  opacity="0.35"
                />
              </svg>

              {/* Central Core Node */}
              <div className="manifestation-core-node">
                <span className="core-top-accent" />
                <span className="core-tag">Root Origin</span>
                <span className="core-title">{mapTitle}</span>
              </div>

              {/* Manifesting Branch 1: Left */}
              <div className="manifesting-branch branch-pos-left">
                <span className="branch-top-strip peach" />
                <div className="branch-header-row">
                  <span className="pill-dot pill-dot-peach" />
                  <span className="branch-title">Foundational Elements</span>
                </div>
                <span className="branch-subtitle">Taxonomy &amp; Core Drivers</span>
              </div>

              {/* Manifesting Branch 2: Right */}
              <div className="manifesting-branch branch-pos-right">
                <span className="branch-top-strip teal" />
                <div className="branch-header-row">
                  <span className="pill-dot pill-dot-cyan" />
                  <span className="branch-title">Applied Synthesis</span>
                </div>
                <span className="branch-subtitle">Architecture &amp; Flow</span>
              </div>

              {/* Manifesting Branch 3: Ghosted */}
              <div className="manifesting-branch branch-pos-ghosted">
                <div className="branch-header-row">
                  <span className="pill-dot" style={{ backgroundColor: 'var(--outline)' }} />
                  <span className="branch-title" style={{ color: 'var(--outline)' }}>
                    Exploratory Vector
                  </span>
                </div>
                <span className="branch-subtitle">Future Trajectories</span>
              </div>

              {/* Spatial Coordinates Dimension Badge */}
              <div className="spatial-marker-pill">
                <span className="material-symbols-outlined" style={{ fontSize: '13px' }}>
                  crop_free
                </span>
                <span>x: 1042.4 · y: 684.0 · 3 nodes</span>
              </div>
            </div>
          </div>

          {/* Console Footer & Cancel Action */}
          <div className="console-footer-bar">
            <div className="contemplative-quote">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: '16px', color: 'var(--primary-container)' }}
              >
                format_quote
              </span>
              <span>Ideas become clearer when given space and relationship.</span>
            </div>

            <button
              type="button"
              className="btn-cancel-generation"
              onClick={handleCancel}
              disabled={isCancelling}
            >
              {isCancelling ? (
                <>
                  <span
                    className="material-symbols-outlined step-spin-icon"
                    style={{ fontSize: '14px' }}
                  >
                    sync
                  </span>
                  <span>Halting synthesis...</span>
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                    close
                  </span>
                  <span>Cancel generation</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Diagnostics Subpanel */}
        <div className="diagnostics-panel">
          <div className="diagnostics-left">
            <span className="diagnostics-dot" />
            <span>Lexical Parser v4.1 / Graph Hierarchy Engine</span>
          </div>
          <div className="diagnostics-latency">LATENCY ~240ms</div>
        </div>
      </main>

      {/* Shared Footer */}
      <AppFooter />
    </div>
  );
}
