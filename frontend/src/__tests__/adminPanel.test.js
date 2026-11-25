/**
 * Unit tests for S3-FE1 Admin Management & Analytics Panel.
 *
 * Runs in plain Vitest Node environment without @testing-library/react,
 * testing admin routing logic, permissions, service methods, and view states.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { adminService } from '../services/adminService'
import { apiClient } from '../services/apiClient'

vi.mock('../services/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}))
import { AdminErrorBoundary } from '../pages/admin/AdminErrorBoundary'

// Route evaluation helper matching AdminRoute decision logic
export function evaluateAdminAccess(user, isInitializing) {
  if (isInitializing) {
    return { status: 'loading' }
  }
  if (!user) {
    return { status: 'redirect', target: '/login' }
  }
  if (user.role !== 'admin') {
    return { status: 'redirect', target: '/dashboard' }
  }
  return { status: 'allowed' }
}

describe('S3-FE1: Admin Route Guard Logic', () => {
  it('returns loading status while auth session is initializing', () => {
    const res = evaluateAdminAccess(null, true)
    expect(res.status).toBe('loading')
  })

  it('redirects unauthenticated visitors to /login', () => {
    const res = evaluateAdminAccess(null, false)
    expect(res.status).toBe('redirect')
    expect(res.target).toBe('/login')
  })

  it('redirects authenticated standard users (role: "user") to /dashboard', () => {
    const standardUser = { id: 'usr-101', email: 'user@mindweave.craft', role: 'user' }
    const res = evaluateAdminAccess(standardUser, false)
    expect(res.status).toBe('redirect')
    expect(res.target).toBe('/dashboard')
  })

  it('permits access when user has administrative role (role: "admin")', () => {
    const adminUser = { id: 'admin-01', email: 'admin@mindweave.craft', role: 'admin' }
    const res = evaluateAdminAccess(adminUser, false)
    expect(res.status).toBe('allowed')
  })
})

describe('S3-FE1: Admin Service & Privacy Enforcement', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads paginated users from the API', async () => {
    const users = [{ id: 'usr-1', email: 'user@example.test', status: 'active', map_count: 2 }]
    apiClient.get.mockResolvedValueOnce({ ok: true, data: users })
    expect(await adminService.getUsers(20, 20)).toEqual(users)
    expect(apiClient.get).toHaveBeenCalledWith('/api/admin/users?limit=20&offset=20')
  })

  it('loads real analytics and audit records through their canonical routes', async () => {
    const statistics = { map_generation: { maps_generated_last_7_days: 0 } }
    const audit = [{ action: 'user.suspend', target_user_id: 'usr-1', actor_id: 'admin-1' }]
    apiClient.get.mockResolvedValueOnce({ ok: true, data: statistics })
    apiClient.get.mockResolvedValueOnce({ ok: true, data: audit })
    expect(await adminService.getAnalytics()).toEqual(statistics)
    expect(await adminService.getAuditLogs()).toEqual(audit)
    expect(apiClient.get).toHaveBeenNthCalledWith(1, '/api/admin/stats')
    expect(apiClient.get).toHaveBeenNthCalledWith(2, '/api/admin/audit-log')
  })

  it('propagates a rejected suspension and uses the canonical POST routes', async () => {
    apiClient.post.mockResolvedValueOnce({ ok: false, error: 'Permission denied' })
    apiClient.post.mockResolvedValueOnce({ ok: true, data: { status: 'active' } })
    await expect(adminService.setUserStatus('usr-1', 'suspended')).rejects.toThrow(
      'Permission denied',
    )
    expect(await adminService.setUserStatus('usr-1', 'active')).toEqual({ status: 'active' })
    expect(apiClient.post).toHaveBeenNthCalledWith(1, '/api/admin/users/usr-1/suspend')
    expect(apiClient.post).toHaveBeenNthCalledWith(2, '/api/admin/users/usr-1/unsuspend')
  })
})

describe('S3-FE1: Admin Error Boundary', () => {
  it('correctly catches errors and updates derived state', () => {
    const error = new Error('Test crash in Admin Panel')
    const derived = AdminErrorBoundary.getDerivedStateFromError(error)
    expect(derived.hasError).toBe(true)
    expect(derived.error).toBe(error)
  })
})
