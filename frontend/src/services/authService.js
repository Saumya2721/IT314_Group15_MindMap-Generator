import {
  apiClient,
  clearStoredAuth,
  getStoredToken,
  getStoredUser,
  setStoredToken,
  setStoredUser,
} from './apiClient'
import { getUserAvatar } from '../utils/formatters'

export const authService = {
  /**
   * User login with email & password
   * Endpoint: POST /auth/login
   */
  async login(email, password, remember = true) {
    const res = await apiClient.post('/auth/login', { email, password })
    if (!res.ok) {
      return {
        success: false,
        error: res.error || 'Invalid email or password.',
      }
    }

    const data = res.data || {}
    const accessToken = data.access_token
    if (accessToken) {
      setStoredToken(accessToken, remember)
    }

    const user = {
      id: data.user?.id || `usr_${Date.now()}`,
      name: data.user?.name || data.user?.email?.split('@')[0] || 'Curator',
      email: data.user?.email || email,
    }
    user.avatar = getUserAvatar(user)

    setStoredUser(user, remember)

    return {
      success: true,
      user,
      token: accessToken,
    }
  },

  /**
   * User registration
   * Endpoint: POST /auth/signup
   */
  async register(name, email, password) {
    const res = await apiClient.post('/auth/signup', {
      email,
      password,
      name: name ? name.trim() : undefined,
    })

    if (!res.ok) {
      return {
        success: false,
        error: res.error || 'Registration failed. Please try again.',
      }
    }

    const data = res.data || {}
    return {
      success: true,
      message:
        data.message ||
        (data.email_confirmation_required
          ? 'Account created! Please check your email to confirm registration.'
          : 'Account registered successfully! Please log in.'),
      user: data.user,
      emailConfirmationRequired: data.email_confirmation_required,
    }
  },

  /**
   * User logout
   * Endpoint: POST /auth/logout
   */
  async logout() {
    try {
      await apiClient.post('/auth/logout')
    } catch (e) {
      console.warn('Logout network notice:', e)
    } finally {
      clearStoredAuth()
    }
    return { success: true }
  },

  /**
   * Fetch currently authenticated profile
   * Endpoint: GET /me
   */
  async getMe() {
    const res = await apiClient.get('/me')
    if (!res.ok) {
      if (res.status === 401) {
        clearStoredAuth()
      }
      return { success: false, status: res.status, error: res.error }
    }

    const data = res.data || {}
    const user = {
      id: data.id,
      email: data.email,
      name: data.profile?.name || data.email?.split('@')[0] || 'Curator',
    }
    user.avatar = getUserAvatar(user)

    setStoredUser(user)
    return { success: true, user }
  },

  /**
   * Google OAuth sign-in flow — redirects user to Google
   * Endpoint: GET /auth/google
   */
  async loginWithGoogle() {
    const res = await apiClient.get('/auth/google')
    if (res.ok && res.data?.google_auth_url) {
      window.location.href = res.data.google_auth_url
      return { success: true }
    }
    return {
      success: false,
      error: res.error || 'Google authentication service is currently unavailable.',
    }
  },

  /**
   * Exchange OAuth authorization code for a session (called from /auth/callback page)
   * Backend sets HttpOnly cookies on success.
   * Endpoint: POST /auth/google/callback
   */
  async exchangeGoogleCode(code) {
    const res = await apiClient.post('/auth/google/callback', { code })
    if (!res.ok) {
      return {
        success: false,
        error: res.error || 'Google sign-in failed. The authorization code may have expired.',
      }
    }
    const data = res.data || {}
    const accessToken = data.access_token
    if (accessToken) {
      setStoredToken(accessToken, true)
    }
    const user = {
      id: data.user?.id,
      email: data.user?.email,
      name: data.user?.name || data.user?.email?.split('@')[0] || 'Curator',
    }
    user.avatar = getUserAvatar(user)
    setStoredUser(user)
    return { success: true, user, token: accessToken }
  },

  /**
   * Request password reset instructions email
   * Endpoint: POST /auth/forgot-password
   */
  async forgotPassword(email) {
    const res = await apiClient.post('/auth/forgot-password', { email })
    if (!res.ok) {
      return {
        success: false,
        error: res.error || 'Could not send password reset email.',
      }
    }
    return {
      success: true,
      message: res.data?.message || 'Password reset instructions sent to your email.',
    }
  },

  /**
   * Complete password reset with token
   * Endpoint: POST /auth/reset-password
   */
  async resetPassword(token, newPassword) {
    const res = await apiClient.post(
      '/auth/reset-password',
      { new_password: newPassword },
      { headers: { Authorization: `Bearer ${token}` } },
    )

    if (!res.ok) {
      return {
        success: false,
        error: res.error || 'Failed to reset password. The link may have expired.',
      }
    }

    return {
      success: true,
      message: res.data?.message || 'Password has been successfully reset. You can now log in.',
    }
  },

  getCurrentUser() {
    return getStoredUser()
  },

  getAccessToken() {
    return getStoredToken()
  },

  clearSession() {
    clearStoredAuth()
  },
}
