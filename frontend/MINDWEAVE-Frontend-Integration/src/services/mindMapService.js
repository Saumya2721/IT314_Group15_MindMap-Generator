import { MOCK_MIND_MAPS } from '../data/mockData.js';

let maps = [...MOCK_MIND_MAPS];

export const mindMapService = {
  async getMaps() {
    await new Promise((resolve) => setTimeout(resolve, 200));
    return [...maps];
  },

  async getMapById(id) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    return maps.find((m) => m.id === id) || maps[0];
  },

  async createMap({ title, rawNotes }) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    const newMap = {
      id: `map_${Date.now()}`,
      title: title || 'Untitled Map',
      category: 'Research',
      categoryColor: '#00454a',
      nodeCount: 8,
      lastEdited: 'Just now',
      bookmarked: false,
      rawNotes,
    };
    maps = [newMap, ...maps];
    return newMap;
  },

  async toggleBookmark(id) {
    maps = maps.map((m) =>
      m.id === id ? { ...m, bookmarked: !m.bookmarked } : m
    );
    return maps.find((m) => m.id === id);
  },
};
