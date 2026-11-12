import React from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import { MindMapProvider } from './context/MindMapContext'

import LoginPage from './pages/LoginPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import OAuthCallbackPage from './pages/OAuthCallbackPage'
import DashboardPage from './pages/DashboardPage'
import CreateMapPage from './pages/CreateMapPage'
import GeneratingPage from './pages/GeneratingPage'
import EditorPage from './pages/EditorPage'

function ProtectedRoute({ children }) {
  const { user, isInitializing } = useAuth()
  const location = useLocation()

  if (isInitializing) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          backgroundColor: 'var(--surface)',
        }}
      >
        <span
          className="material-symbols-outlined step-spin-icon"
          style={{ fontSize: '32px', color: 'var(--primary)' }}
        >
          sync
        </span>
        <p
          style={{
            marginTop: '12px',
            color: 'var(--outline)',
            fontFamily: 'var(--font-headline)',
            fontSize: '15px',
          }}
        >
          Restoring cognitive workspace...
        </p>
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return children
}

function PublicAuthRoute({ children }) {
  const { user, isInitializing } = useAuth()
  const location = useLocation()

  if (isInitializing) {
    return null
  }

  if (user) {
    const from = location.state?.from?.pathname || '/dashboard'
    return <Navigate to={from} replace />
  }

  return children
}

export default function App() {
  return (
    <AuthProvider>
      <MindMapProvider>
        <Router>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route
              path="/login"
              element={
                <PublicAuthRoute>
                  <LoginPage />
                </PublicAuthRoute>
              }
            />
            <Route path="/reset-password" element={<ResetPasswordPage />} />
            {/* Google OAuth callback — must be before the wildcard * route */}
            <Route path="/auth/callback" element={<OAuthCallbackPage />} />
            <Route
              path="/dashboard"
              element={
                <ProtectedRoute>
                  <DashboardPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/create"
              element={
                <ProtectedRoute>
                  <CreateMapPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/generating"
              element={
                <ProtectedRoute>
                  <GeneratingPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/editor/:id"
              element={
                <ProtectedRoute>
                  <EditorPage />
                </ProtectedRoute>
              }
            />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Router>
      </MindMapProvider>
    </AuthProvider>
  )
}
