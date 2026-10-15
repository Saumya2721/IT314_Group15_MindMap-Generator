import { MOCK_USER } from '../data/mockData.js';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
const STORAGE_USER_KEY = 'mindweave_auth_user';

/**
 * Safely parse error messages from FastAPI responses
 */
function parseErrorMessage(data, defaultMsg) {
  if (!data) return defaultMsg;
  if (typeof data.detail === 'string') {
    return data.detail;
  }
  if (Array.isArray(data.detail) && data.detail.length > 0) {
    return data.detail.map((item) => item.msg || JSON.stringify(item)).join(', ');
  }
  if (typeof data.message === 'string') {
    return data.message;
  }
  return defaultMsg;
}

export const authService = {
  /**
   * Real backend authentication with email and password
   * Endpoint: POST /auth/login
   */
  async login(email, password) {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({ email, password }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        return {
          success: false,
          error: parseErrorMessage(data, 'Invalid Email or Password'),
        };
      }

      // Backend returns { access_token, refresh_token, token_type, user: { id, email } }
      // and sets HttpOnly cookies (access_token, refresh_token).
      // We store non-sensitive user metadata in sessionStorage so the UI stays active in the current tab.
      // We deliberately do NOT store access tokens in localStorage, relying on the secure HttpOnly cookie.
      const user = {
        id: data.user?.id || `usr_${Date.now()}`,
        name: data.user?.name || data.user?.email?.split('@')[0] || 'Curator',
        email: data.user?.email || email,
        avatar: MOCK_USER.avatar,
      };

      try {
        sessionStorage.setItem(STORAGE_USER_KEY, JSON.stringify(user));
      } catch (storageErr) {
        console.warn('Session storage write failed:', storageErr);
      }

      return {
        success: true,
        user,
      };
    } catch (err) {
      return {
        success: false,
        error: `Unable to connect to authentication server (${API_BASE_URL}). Please verify the backend is running.`,
      };
    }
  },

  /**
   * Real backend registration
   * Endpoint: POST /auth/signup
   */
  async register(name, email, password) {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/signup`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          email,
          password,
          name: name ? name.trim() : undefined,
        }),
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        return {
          success: false,
          error: parseErrorMessage(data, 'Registration failed. Please try again.'),
        };
      }

      return {
        success: true,
        message:
          data.message ||
          (data.email_confirmation_required
            ? 'Account created! Please check your email to confirm registration.'
            : 'Account registered successfully! Please log in.'),
        user: data.user,
        emailConfirmationRequired: data.email_confirmation_required,
      };
    } catch (err) {
      return {
        success: false,
        error: `Unable to connect to authentication server (${API_BASE_URL}). Please verify the backend is running.`,
      };
    }
  },

  /**
   * Real backend logout
   * Endpoint: POST /auth/logout
   */
  async logout() {
    try {
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch (err) {
      console.warn('Logout request failed or backend unreachable:', err);
    } finally {
      try {
        sessionStorage.removeItem(STORAGE_USER_KEY);
      } catch (storageErr) {
        console.warn('Session storage clear failed:', storageErr);
      }
    }
    return { success: true };
  },

  /**
   * Fetch current authenticated profile from backend
   * Endpoint: GET /me
   */
  async getMe() {
    try {
      const response = await fetch(`${API_BASE_URL}/me`, {
        method: 'GET',
        credentials: 'include',
      });

      if (response.status === 401) {
        this.clearSession();
        return { success: false, status: 401 };
      }

      if (!response.ok) {
        return { success: false, status: response.status };
      }

      const data = await response.json();
      const user = {
        id: data.id,
        email: data.email,
        name: data.profile?.name || data.email?.split('@')[0] || 'Curator',
        avatar: MOCK_USER.avatar,
      };

      try {
        sessionStorage.setItem(STORAGE_USER_KEY, JSON.stringify(user));
      } catch {
        // ignore
      }

      return { success: true, user };
    } catch {
      return { success: false };
    }
  },

  /**
   * Google OAuth sign-in flow
   * Endpoint: GET /auth/google
   */
  async loginWithGoogle() {
    try {
      const response = await fetch(`${API_BASE_URL}/auth/google`, {
        method: 'GET',
        credentials: 'include',
      });

      const data = await response.json().catch(() => ({}));
      if (response.ok && data.google_auth_url) {
        window.location.href = data.google_auth_url;
        return { success: true };
      }
      return {
        success: false,
        error: parseErrorMessage(data, 'Google authentication unavailable'),
      };
    } catch (err) {
      return {
        success: false,
        error: `Could not reach authentication server (${API_BASE_URL})`,
      };
    }
  },

  /**
   * Get current user from active session storage.
   * Returns null if no authenticated user session exists.
   */
  getCurrentUser() {
    try {
      const raw = sessionStorage.getItem(STORAGE_USER_KEY);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch {
      // ignore
    }
    return null;
  },

  /**
   * Clear local session storage
   */
  clearSession() {
    try {
      sessionStorage.removeItem(STORAGE_USER_KEY);
    } catch {
      // ignore
    }
  },
};
