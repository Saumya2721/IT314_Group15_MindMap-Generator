// Mock data for Sprint 1 (Mind Map Generator from Raw Ideas)

export const MOCK_USER = {
  id: 'usr_01',
  name: 'Curator',
  email: 'curator@mindweave.craft',
  avatar: 'https://lh3.googleusercontent.com/aida-public/AB6AXuB5NFQwx9Y0rkms3wT-alCi2zxtqHCZTAyMjKmP-sM-J9ndHLFRGNXSNIIJoy3udUkvYoWpJx3UparXK04g0--C0qgINk6I23Gr51wKOCjjTxhG6omD1AYLepqxSr4wUeUe39GJz6LhROot39TPah3fin69PBU9KXsoLCY2Fo2FO1NCISm6AXUrQyU6lR4KkwE-b4sTXAkCvVxebm23xefPgmr7PxjecBiZeQ2FpHHdTOOgkS9HQo87',
};

export const MOCK_MIND_MAPS = [
  {
    id: 'map_01',
    title: 'Machine Learning',
    category: 'Research',
    categoryColor: '#00454a',
    nodeCount: 14,
    lastEdited: '2h ago',
    bookmarked: true,
    previewData: {
      core: 'ML Core',
      coreColor: '#00454a',
      branches: [
        { label: 'Supervised', x: 40, y: 30, color: '#00454a', side: 'left' },
        { label: 'Unsupervised', x: 40, y: 70, color: '#994629', side: 'left' },
        { label: 'Reinforce', x: 160, y: 30, color: '#155e63', side: 'right' },
        { label: 'Neural Nets', x: 160, y: 70, color: '#994629', side: 'right' },
      ],
    },
  },
  {
    id: 'map_02',
    title: 'College Fest',
    category: 'Event',
    categoryColor: '#523900',
    nodeCount: 22,
    lastEdited: 'Yesterday',
    bookmarked: false,
    previewData: {
      core: 'Fest 2024',
      coreColor: '#704f00',
      branches: [
        { label: 'Logistics', x: 40, y: 30, color: '#523900', side: 'left' },
        { label: 'Creative', x: 40, y: 70, color: '#523900', side: 'left' },
        { label: 'Sponsors', x: 160, y: 30, color: '#704f00', side: 'right' },
        { label: 'Tech Ops', x: 160, y: 70, color: '#704f00', side: 'right' },
      ],
    },
  },
  {
    id: 'map_03',
    title: 'Software Eng.',
    category: 'University',
    categoryColor: '#6f7979',
    nodeCount: 19,
    lastEdited: '3d ago',
    bookmarked: true,
    previewData: {
      core: 'Sys Arch',
      coreColor: '#3f4849',
      branches: [
        { label: 'Structure', x: 40, y: 30, color: '#6f7979', side: 'left' },
        { label: 'CI / CD', x: 40, y: 70, color: '#6f7979', side: 'left' },
        { label: 'Database', x: 160, y: 30, color: '#6f7979', side: 'right' },
        { label: 'Testing', x: 160, y: 70, color: '#6f7979', side: 'right' },
      ],
    },
  },
  {
    id: 'map_04',
    title: 'Startup Idea',
    category: 'Draft',
    categoryColor: '#994629',
    nodeCount: 11,
    lastEdited: 'Oct 14',
    bookmarked: false,
    previewData: {
      core: 'Venture',
      coreColor: '#994629',
      branches: [
        { label: 'Problem', x: 40, y: 30, color: '#994629', side: 'left' },
        { label: 'Solution', x: 40, y: 70, color: '#994629', side: 'left' },
        { label: 'Go-Market', x: 160, y: 30, color: '#994629', side: 'right' },
        { label: 'Revenue', x: 160, y: 70, color: '#994629', side: 'right' },
      ],
    },
  },
];
