import React, { createContext, useContext, useState, useEffect } from 'react'
import { authService } from '../services/authService'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(authService.getCurrentUser())
  const [loading, setLoading] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)

  useEffect(() => {
    const initAuth = async () => {
      try {
        // Attempt cookie-anchored session hydration on initial mount/reopen
        const meRes = await authService.getMe()
        if (meRes.success && meRes.user) {
          setUser(meRes.user)
        } else if (meRes.status === 401) {
          setUser(null)
        }
      } catch (err) {
        console.warn('Initial session check failed:', err)
        setUser(null)
      } finally {
        setIsInitializing(false)
      }
    }
    initAuth()
  }, [])

  const login = async (email, password) => {
    setLoading(true)
    try {
      const res = await authService.login(email, password)
      if (res.success && res.user) {
        setUser(res.user)
      }
      return res
    } finally {
      setLoading(false)
    }
  }

  const register = async (name, email, password) => {
    setLoading(true)
    try {
      const res = await authService.register(name, email, password)
      return res
    } finally {
      setLoading(false)
    }
  }

  const loginWithGoogle = async () => {
    setLoading(true)
    try {
      const res = await authService.loginWithGoogle()
      if (res.success && res.user) {
        setUser(res.user)
      }
      return res
    } finally {
      setLoading(false)
    }
  }

  /**
   * Called by OAuthCallbackPage after the backend exchanges the code and sets HttpOnly cookies.
   * Re-fetches /me to confirm the live session before updating the auth context.
   */
  const hydrateFromOAuth = async () => {
    try {
      const meRes = await authService.getMe()
      if (meRes.success && meRes.user) {
        setUser(meRes.user)
        return { success: true, user: meRes.user }
      }
      return { success: false, error: 'Session could not be confirmed after Google sign-in.' }
    } catch {
      return { success: false, error: 'Session hydration failed after Google sign-in.' }
    }
  }

  const logout = async () => {
    setLoading(true)
    try {
      await authService.logout()
    } finally {
      setUser(null)
      setLoading(false)
    }
  }

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isInitializing,
        login,
        register,
        loginWithGoogle,
        hydrateFromOAuth,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
