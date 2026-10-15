import React, { useState, useRef, useEffect } from 'react';
import { NodeInspector } from './NodeInspector';
import { CanvasDock } from './CanvasDock';
import './MindMapCanvas.css';

export function MindMapCanvas({
  initialTitle = 'Machine Learning',
  initialThoughts = '',
  toastMessage = '',
  onClearToast,
}) {
  const containerRef = useRef(null);

  // Canvas pan & zoom state
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(100);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });

  // Selected node state for inspector flyout
  const [selectedNodeId, setSelectedNodeId] = useState('node_branch_1');

  // Interactive nodes graph
  const [nodes, setNodes] = useState(() => {
    const isRenewable = initialTitle.toLowerCase().includes('renewable') || initialThoughts.toLowerCase().includes('photovoltaic');

    if (isRenewable) {
      return [
        {
          id: 'node_core',
          type: 'core',
          title: initialTitle,
          subtitle: 'Holistic framework for clean generation, localized storage, and intelligent grid balancing.',
          x: 60,
          y: 280,
          tint: '#155E63',
        },
        {
          id: 'node_branch_1',
          type: 'branch',
          parentId: 'node_core',
          category: 'Generation',
          title: 'Photovoltaic Systems',
          subtitle: 'Perovskite tandem cells reaching 33.7% laboratory efficiency.',
          x: 480,
          y: 130,
          tint: '#C96B4B',
        },
        {
          id: 'node_leaf_1',
          type: 'leaf',
          parentId: 'node_branch_1',
          category: 'Tandem Cells',
          title: 'Efficiency Frontiers',
          subtitle: 'Multi-junction layered absorption.',
          x: 840,
          y: 95,
          tint: '#C96B4B',
        },
        {
          id: 'node_leaf_2',
          type: 'leaf',
          parentId: 'node_branch_1',
          category: 'Durability',
          title: 'Thermal Degradation',
          subtitle: 'Encapsulation against moisture stress.',
          x: 840,
          y: 195,
          tint: '#C96B4B',
        },
        {
          id: 'node_branch_2',
          type: 'branch',
          parentId: 'node_core',
          category: 'Storage',
          title: 'Decentralized Hubs',
          subtitle: 'Solid-state battery hubs vs localized flow batteries for microgrids.',
          x: 480,
          y: 310,
          tint: '#155E63',
        },
        {
          id: 'node_leaf_3',
          type: 'leaf',
          parentId: 'node_branch_2',
          category: 'Microgrids',
          title: 'Flow Chemistries',
          subtitle: 'Vanadium redox for long-duration reserve.',
          x: 840,
          y: 310,
          tint: '#155E63',
        },
        {
          id: 'node_leaf_4',
          type: 'leaf',
          parentId: 'node_branch_2',
          category: 'Telemetry',
          title: 'Inverter Telemetry',
          subtitle: 'Virtual Power Plants (VPPs) dispatch.',
          x: 840,
          y: 410,
          tint: '#155E63',
        },
        {
          id: 'node_branch_3',
          type: 'branch',
          parentId: 'node_core',
          category: 'Distribution',
          title: 'Grid Stabilization',
          subtitle: 'Cross-regional transmission line permitting & queue management.',
          x: 480,
          y: 490,
          tint: '#899B72',
        },
        {
          id: 'node_leaf_5',
          type: 'leaf',
          parentId: 'node_branch_3',
          category: 'Regulation',
          title: 'Interconnection',
          subtitle: 'Streamlining sub-station approvals.',
          x: 840,
          y: 505,
          tint: '#899B72',
        },
        {
          id: 'node_leaf_6',
          type: 'leaf',
          parentId: 'node_branch_3',
          category: 'Supply Chain',
          title: 'Sodium-Ion Shift',
          subtitle: 'Ethical displacement of rare earth elements.',
          x: 840,
          y: 605,
          tint: '#899B72',
        },
      ];
    }

    // Default: Machine Learning graph from Stitch mockup
    return [
      {
        id: 'node_core',
        type: 'core',
        title: initialTitle || 'Machine Learning',
        subtitle: 'Algorithmic pattern recognition and generalizable empirical inference.',
        x: 60,
        y: 280,
        tint: '#155E63',
      },
      {
        id: 'node_branch_1',
        type: 'branch',
        parentId: 'node_core',
        category: 'Supervised',
        title: 'Supervised Learning',
        subtitle: 'Inductive bias inferred from ground-truth labeled training data.',
        x: 480,
        y: 130,
        tint: '#C96B4B',
      },
      {
        id: 'node_leaf_1',
        type: 'leaf',
        parentId: 'node_branch_1',
        category: 'Continuous',
        title: 'Regression',
        subtitle: 'Predicting continuous targets with regularized loss metrics.',
        x: 840,
        y: 95,
        tint: '#C96B4B',
      },
      {
        id: 'node_leaf_2',
        type: 'leaf',
        parentId: 'node_branch_1',
        category: 'Discrete',
        title: 'Classification',
        subtitle: 'Boundary partitioning into discrete categorical classes.',
        x: 840,
        y: 195,
        tint: '#C96B4B',
      },
      {
        id: 'node_branch_2',
        type: 'branch',
        parentId: 'node_core',
        category: 'Unsupervised',
        title: 'Unsupervised',
        subtitle: 'Latent manifold geometry & inherent clustering affinity discovery.',
        x: 480,
        y: 310,
        tint: '#155E63',
      },
      {
        id: 'node_leaf_3',
        type: 'leaf',
        parentId: 'node_branch_2',
        category: 'Topology',
        title: 'Clustering',
        subtitle: 'Partitioning high-dimensional points into density groups.',
        x: 840,
        y: 310,
        tint: '#155E63',
      },
      {
        id: 'node_leaf_4',
        type: 'leaf',
        parentId: 'node_branch_2',
        category: 'Projection',
        title: 'Dimensionality',
        subtitle: 'Principal variance preservation via non-linear subspace.',
        x: 840,
        y: 410,
        tint: '#155E63',
      },
      {
        id: 'node_branch_3',
        type: 'branch',
        parentId: 'node_core',
        category: 'Reinforcement',
        title: 'Reinforcement',
        subtitle: 'Markov Decision Processes optimized by delayed rewards.',
        x: 480,
        y: 490,
        tint: '#899B72',
      },
      {
        id: 'node_leaf_5',
        type: 'leaf',
        parentId: 'node_branch_3',
        category: 'Actors',
        title: 'Policy & Value',
        subtitle: 'Balancing exploitation vs exploration across trajectories.',
        x: 840,
        y: 505,
        tint: '#899B72',
      },
      {
        id: 'node_leaf_6',
        type: 'leaf',
        parentId: 'node_branch_3',
        category: 'Bellman',
        title: 'Reward Optimization',
        subtitle: 'Temporal difference learning with discounted return horizons.',
        x: 840,
        y: 605,
        tint: '#899B72',
      },
    ];
  });

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;

  // Pan gestures
  const handleMouseDown = (e) => {
    if (e.target.closest('button, input, textarea, a, .atelier-node-card, .node-inspector-flyout, .canvas-control-dock')) {
      return;
    }
    setIsDragging(true);
    dragStartRef.current = {
      x: e.clientX - pan.x,
      y: e.clientY - pan.y,
    };
  };

  const handleMouseMove = (e) => {
    if (!isDragging) return;
    setPan({
      x: e.clientX - dragStartRef.current.x,
      y: e.clientY - dragStartRef.current.y,
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  // Zoom controls
  const handleZoomIn = () => setZoom((z) => Math.min(z + 15, 175));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 15, 50));
  const handleFitScreen = () => {
    setZoom(100);
    setPan({ x: 0, y: 0 });
  };

  // Auto layout reset
  const handleAutoLayout = () => {
    setPan({ x: 0, y: 0 });
    setZoom(100);
  };

  // Node editing handlers
  const handleUpdateNodeTitle = (id, newTitle) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, title: newTitle } : n))
    );
  };

  const handleUpdateNodeDesc = (id, newDesc) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, subtitle: newDesc } : n))
    );
  };

  const handleUpdateNodeTint = (id, newTint) => {
    setNodes((prev) =>
      prev.map((n) => (n.id === id ? { ...n, tint: newTint } : n))
    );
  };

  // Add child node
  const handleAddChildNode = (parentId) => {
    const parent = nodes.find((n) => n.id === parentId);
    if (!parent) return;

    const newId = `node_leaf_${Date.now()}`;
    const newChild = {
      id: newId,
      type: 'leaf',
      parentId: parent.id,
      category: 'Child',
      title: 'New Sub-topic',
      subtitle: 'Added from inspector.',
      x: parent.x + 320,
      y: parent.y + 40,
      tint: parent.tint,
    };

    setNodes((prev) => [...prev, newChild]);
    setSelectedNodeId(newId);
  };

  // AI Expand
  const handleAiExpand = (nodeId) => {
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;

    const expand1 = {
      id: `node_expand_${Date.now()}_1`,
      type: 'leaf',
      parentId: node.id,
      category: 'Synthesis',
      title: `Analysis of ${node.title.slice(0, 15)}`,
      subtitle: 'Generated via cognitive reasoning.',
      x: node.x + 320,
      y: node.y - 30,
      tint: node.tint,
    };

    const expand2 = {
      id: `node_expand_${Date.now()}_2`,
      type: 'leaf',
      parentId: node.id,
      category: 'Perspective',
      title: `Key Drivers & Trajectory`,
      subtitle: 'Topological extension.',
      x: node.x + 320,
      y: node.y + 60,
      tint: node.tint,
    };

    setNodes((prev) => [...prev, expand1, expand2]);
  };

  // Add a new floating node to canvas
  const handleAddFreeNode = () => {
    const newId = `node_custom_${Date.now()}`;
    const newNode = {
      id: newId,
      type: 'branch',
      parentId: 'node_core',
      category: 'New Idea',
      title: 'New Focus Area',
      subtitle: 'Double click or select to edit.',
      x: 480,
      y: 640,
      tint: '#155E63',
    };
    setNodes((prev) => [...prev, newNode]);
    setSelectedNodeId(newId);
  };

  return (
    <div
      className="canvas-viewport-container"
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Toast Alert Feedback */}
      {toastMessage && (
        <div className="canvas-toast-alert">
          <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
            check_circle
          </span>
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Gentle 28px Dot Grid Canvas Matrix */}
      <svg className="canvas-grid-pattern" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="quiet-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <circle cx="2" cy="2" r="1.1" fill="#6f7979" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#quiet-grid)" />
      </svg>

      {/* Inner Pan & Zoom Plane */}
      <div
        className="canvas-transform-plane"
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom / 100})`,
        }}
      >
        {/* Dynamic Curved Bézier Connectors SVG Layer */}
        <svg className="canvas-connectors-layer">
          {nodes
            .filter((n) => n.parentId)
            .map((child) => {
              const parent = nodes.find((p) => p.id === child.parentId);
              if (!parent) return null;

              // Node dimensions for connection anchors
              const parentWidth = parent.type === 'core' ? 220 : parent.type === 'branch' ? 250 : 260;
              const parentHeight = parent.type === 'core' ? 120 : parent.type === 'branch' ? 100 : 70;
              const childHeight = child.type === 'branch' ? 100 : 70;

              const startX = parent.x + parentWidth;
              const startY = parent.y + parentHeight / 2;
              const endX = child.x;
              const endY = child.y + childHeight / 2;

              const deltaX = (endX - startX) * 0.55;
              const pathD = `M ${startX} ${startY} C ${startX + deltaX} ${startY}, ${endX - deltaX} ${endY}, ${endX} ${endY}`;

              const isSelectedPath = selectedNodeId === child.id || selectedNodeId === parent.id;
              const strokeColor = child.tint || '#155E63';
              const isDashed = child.type === 'leaf';

              return (
                <g key={`edge_${parent.id}_${child.id}`}>
                  <path
                    d={pathD}
                    fill="none"
                    stroke={strokeColor}
                    strokeWidth={isSelectedPath ? 2 : 1.5}
                    strokeDasharray={isDashed ? '3 3' : 'none'}
                    strokeLinecap="round"
                    opacity={isSelectedPath ? 0.95 : 0.65}
                  />
                  {/* Joint terminal anchors */}
                  <circle cx={startX} cy={startY} r={isSelectedPath ? 3.5 : 2.5} fill={strokeColor} />
                  <circle
                    cx={endX}
                    cy={endY}
                    r={isSelectedPath ? 3.5 : 2.5}
                    fill="#FBF9F4"
                    stroke={strokeColor}
                    strokeWidth={isSelectedPath ? 2 : 1.5}
                  />
                </g>
              );
            })}
        </svg>

        {/* Mind Map Nodes Layer */}
        <div className="canvas-nodes-layer">
          {nodes.map((node) => {
            const isSelected = selectedNodeId === node.id;
            const nodeTint = node.tint || '#155E63';

            if (node.type === 'core') {
              return (
                <div
                  key={node.id}
                  className={`atelier-node-card node-variant-core ${isSelected ? 'selected' : ''}`}
                  style={{ left: `${node.x}px`, top: `${node.y}px` }}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <div className="core-tag-badge">
                    <span className="dot" style={{ backgroundColor: nodeTint }} />
                    <span>Core Focus</span>
                  </div>
                  <h1 className="core-node-name">{node.title}</h1>
                  <p className="core-node-desc">{node.subtitle}</p>
                </div>
              );
            }

            if (node.type === 'branch') {
              return (
                <div
                  key={node.id}
                  className={`atelier-node-card node-variant-branch ${isSelected ? 'selected' : ''}`}
                  style={{ left: `${node.x}px`, top: `${node.y}px` }}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <div className="branch-top-bar">
                    <span
                      className="branch-taxonomy-pill"
                      style={{
                        backgroundColor: `${nodeTint}18`,
                        color: nodeTint,
                      }}
                    >
                      <span className="dot" style={{ backgroundColor: nodeTint }} />
                      <span>{node.category || 'Branch'}</span>
                    </span>
                    {isSelected && (
                      <span
                        className="material-symbols-outlined"
                        style={{ fontSize: '16px', color: nodeTint }}
                      >
                        check_circle
                      </span>
                    )}
                  </div>
                  <h2 className="branch-node-name">{node.title}</h2>
                  <p className="branch-node-desc">{node.subtitle}</p>
                </div>
              );
            }

            // Sub-leaf Variant
            return (
              <div
                key={node.id}
                className={`atelier-node-card node-variant-leaf ${isSelected ? 'selected' : ''}`}
                style={{ left: `${node.x}px`, top: `${node.y}px` }}
                onClick={() => setSelectedNodeId(node.id)}
              >
                <div className="leaf-top-bar">
                  <h3 className="leaf-node-name">{node.title}</h3>
                  <span className="leaf-category-stamp">{node.category || 'Topic'}</span>
                </div>
                <p className="leaf-node-desc">{node.subtitle}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Floating Node Inspector Flyout */}
      {selectedNode && (
        <NodeInspector
          selectedNode={selectedNode}
          onClose={() => setSelectedNodeId(null)}
          onUpdateTitle={handleUpdateNodeTitle}
          onUpdateDescription={handleUpdateNodeDesc}
          onUpdateTint={handleUpdateNodeTint}
          onAddChildNode={handleAddChildNode}
          onAiExpand={handleAiExpand}
        />
      )}

      {/* Minimalist Canvas Dock */}
      <CanvasDock
        zoom={zoom}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onFitScreen={handleFitScreen}
        onAutoLayout={handleAutoLayout}
      />
    </div>
  );
}
