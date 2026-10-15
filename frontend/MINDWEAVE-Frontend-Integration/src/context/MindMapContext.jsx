import React, { createContext, useContext, useState, useEffect } from 'react';
import { mindMapService } from '../services/mindMapService.js';

const MindMapContext = createContext(null);

export function MindMapProvider({ children }) {
  const [maps, setMaps] = useState([]);
  const [activeMap, setActiveMap] = useState(null);
  const [draftMap, setDraftMap] = useState({
    topic: 'Renewable Energy Architecture',
    thoughts: `- Photovoltaic generation: Perovskite tandem cells reaching 33.7% laboratory efficiency.
- Decentralized storage architecture: Solid-state battery hubs vs. localized flow batteries for microgrids.
- Grid stabilization: Virtual Power Plants (VPPs) aggregating residential inverter telemetry.
- Supply chain vulnerabilities: Rare earth element extraction ethics, cobalt displacement via sodium-ion chemistry.
- Regulatory bottlenecks: Cross-regional transmission line permitting, interconnection queue stagnation.`,
    files: [],
  });
  const [loading, setLoading] = useState(false);

  const fetchMaps = async () => {
    setLoading(true);
    try {
      const data = await mindMapService.getMaps();
      setMaps(data);
    } finally {
      setLoading(false);
    }
  };

  const createMap = async (data) => {
    setLoading(true);
    try {
      const newMap = await mindMapService.createMap(data);
      setMaps((prev) => [newMap, ...prev]);
      setActiveMap(newMap);
      return newMap;
    } finally {
      setLoading(false);
    }
  };

  const toggleBookmark = async (id) => {
    try {
      const updated = await mindMapService.toggleBookmark(id);
      setMaps((prev) =>
        prev.map((m) => (m.id === id ? { ...m, bookmarked: updated.bookmarked } : m))
      );
    } catch (err) {
      console.error('Failed to toggle bookmark:', err);
    }
  };

  useEffect(() => {
    fetchMaps();
  }, []);

  return (
    <MindMapContext.Provider
      value={{
        maps,
        activeMap,
        draftMap,
        setDraftMap,
        loading,
        fetchMaps,
        createMap,
        toggleBookmark,
        setActiveMap,
      }}
    >
      {children}
    </MindMapContext.Provider>
  );
}

export function useMindMap() {
  const context = useContext(MindMapContext);
  if (!context) {
    throw new Error('useMindMap must be used within a MindMapProvider');
  }
  return context;
}
