import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './context/AuthContext';
import { MindMapProvider } from './context/MindMapContext';

import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import CreateMapPage from './pages/CreateMapPage';
import GeneratingPage from './pages/GeneratingPage';
import EditorPage from './pages/EditorPage';

export default function App() {
  return (
    <AuthProvider>
      <MindMapProvider>
        <Router>
          <Routes>
            <Route path="/" element={<Navigate to="/login" replace />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/create" element={<CreateMapPage />} />
            <Route path="/generating" element={<GeneratingPage />} />
            <Route path="/editor/:id" element={<EditorPage />} />
            <Route path="*" element={<Navigate to="/login" replace />} />
          </Routes>
        </Router>
      </MindMapProvider>
    </AuthProvider>
  );
}
