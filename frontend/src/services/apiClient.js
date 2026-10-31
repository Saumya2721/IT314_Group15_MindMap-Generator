/**
 * Modular, secure API client for MindWeave.
 * Implements dual-defense security:
 * 1. Persistent session via HttpOnly cookies (survives tab/browser closure without XSS exposure).
 * 2. In-memory & tab-scoped sessionStorage token for explicit Authorization Bearer headers.
 * 3. Never writes raw JWT tokens to localStorage.
 * 4. Automatic silent token refresh on 401 using the HttpOnly refresh token cookie.
 */

const STORAGE_TOKEN_KEY = 'mindweave_access_token'
const STORAGE_USER_KEY = 'mindweave_auth_user'

// In Vite dev, proxy routes /api, /auth, /me directly to http://localhost:8000
const API_BASE_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

let _inMemoryToken = null

export function getStoredToken() {
  if (_inMemoryToken) return _inMemoryToken
  try {
    const token = sessionStorage.getItem(STORAGE_TOKEN_KEY)
    if (token) {
      _inMemoryToken = token
      return token
    }
  } catch {
    // sessionStorage unavailable
  }
  return null
}

export function setStoredToken(token) {
  _inMemoryToken = token || null
  try {
    if (token) {
      sessionStorage.setItem(STORAGE_TOKEN_KEY, token)
    } else {
      sessionStorage.removeItem(STORAGE_TOKEN_KEY)
    }
  } catch (e) {
    console.warn('Could not persist token in sessionStorage:', e)
  }
}

export function getStoredUser() {
  try {
    const raw = sessionStorage.getItem(STORAGE_USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function setStoredUser(user) {
  try {
    if (user) {
      sessionStorage.setItem(STORAGE_USER_KEY, JSON.stringify(user))
    } else {
      sessionStorage.removeItem(STORAGE_USER_KEY)
    }
  } catch (e) {
    console.warn('Could not persist user session:', e)
  }
}

export function clearStoredAuth() {
  _inMemoryToken = null
  try {
    sessionStorage.removeItem(STORAGE_TOKEN_KEY)
    sessionStorage.removeItem(STORAGE_USER_KEY)
    // Purge any legacy localStorage keys if previously set
    localStorage.removeItem(STORAGE_TOKEN_KEY)
    localStorage.removeItem(STORAGE_USER_KEY)
  } catch (e) {
    console.warn('Could not clear auth storage:', e)
  }
}

/**
 * Standardized FastAPI error extraction
 */
export function formatApiError(data, fallback = 'Operation failed') {
  if (!data) return fallback
  if (typeof data === 'string') return data
  if (typeof data.detail === 'string') return data.detail
  if (Array.isArray(data.detail) && data.detail.length > 0) {
    return data.detail
      .map((item) =>
        item.msg ? `${item.loc?.slice(-1)[0] || 'field'}: ${item.msg}` : JSON.stringify(item),
      )
      .join('; ')
  }
  if (typeof data.message === 'string') return data.message
  if (typeof data.error === 'string') return data.error
  return fallback
}

/**
 * Core HTTP request handler with silent refresh capability
 */
export async function apiRequest(endpoint, options = {}, isRetry = false) {
  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE_URL}${endpoint}`
  const headers = new Headers(options.headers || {})

  // Automatically attach Bearer token if present in memory/sessionStorage
  const token = getStoredToken()
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  // Ensure Content-Type is application/json unless sending FormData or explicitly omitted
  const isFormData = options.body instanceof FormData
  if (!isFormData && !headers.has('Content-Type') && options.method && options.method !== 'GET') {
    headers.set('Content-Type', 'application/json')
  }

  const fetchOptions = {
    ...options,
    headers,
    credentials: options.credentials || 'include',
  }

  try {
    const response = await fetch(url, fetchOptions)

    // 204 No Content
    if (response.status === 204) {
      return { ok: true, status: 204, data: null }
    }

    // Silent session recovery: if 401 and not an explicit auth route, attempt token refresh via HttpOnly cookie
    if (
      response.status === 401 &&
      !isRetry &&
      !endpoint.includes('/auth/login') &&
      !endpoint.includes('/auth/refresh') &&
      !endpoint.includes('/auth/signup')
    ) {
      try {
        const refreshRes = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
        })
        if (refreshRes.ok) {
          const refreshData = await refreshRes.json()
          if (refreshData?.access_token) {
            setStoredToken(refreshData.access_token)
            // Re-execute request once with fresh token
            return apiRequest(endpoint, options, true)
          }
        } else {
          clearStoredAuth()
        }
      } catch {
        // Refresh network failure, fall through to return original 401
      }
    }

    const contentType = response.headers.get('content-type') || ''
    let data = null
    if (contentType.includes('application/json')) {
      data = await response.json().catch(() => null)
    } else {
      const text = await response.text().catch(() => '')
      data = text ? { raw: text } : null
    }

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: formatApiError(data, `Request failed with status ${response.status}`),
        data,
      }
    }

    return {
      ok: true,
      status: response.status,
      data,
    }
  } catch (err) {
    return {
      ok: false,
      status: 0,
      error: `Network error: Unable to reach server (${url}). Please check your connection.`,
      data: null,
    }
  }
}

export const apiClient = {
  get(endpoint, options = {}) {
    return apiRequest(endpoint, { ...options, method: 'GET' })
  },

  post(endpoint, body = null, options = {}) {
    return apiRequest(endpoint, {
      ...options,
      method: 'POST',
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
  },

  patch(endpoint, body = null, options = {}) {
    return apiRequest(endpoint, {
      ...options,
      method: 'PATCH',
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    })
  },

  delete(endpoint, options = {}) {
    return apiRequest(endpoint, { ...options, method: 'DELETE' })
  },
}
