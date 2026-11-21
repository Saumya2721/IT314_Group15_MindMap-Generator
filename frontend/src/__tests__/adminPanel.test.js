/**
 * Unit tests for S3-FE1 Admin Management & Analytics Panel.
 *
 * Runs in plain Vitest Node environment without @testing-library/react,
 * testing admin routing logic, permissions, service methods, and view states.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { adminService } from '../services/adminService'
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

  it('provides default users metadata without node/edge content', async () => {
    const users = await adminService.getUsers()
    expect(Array.isArray(users)).toBe(true)
    expect(users.length).toBeGreaterThan(0)

    users.forEach((u) => {
      expect(u).toHaveProperty('id')
      expect(u).toHaveProperty('email')
      expect(u).toHaveProperty('status')
      expect(u).toHaveProperty('total_maps_count')
      // Strict privacy check: ensure no map content/nodes/edges are exposed
      expect(u).not.toHaveProperty('nodes')
      expect(u).not.toHaveProperty('edges')
      expect(u).not.toHaveProperty('graph_data')
    })
  })

  it('provides aggregate platform analytics data', async () => {
    const analytics = await adminService.getAnalytics()
    expect(analytics).toBeDefined()
    expect(analytics).toHaveProperty('maps_created_per_day')
    expect(analytics).toHaveProperty('failure_rate')
    expect(analytics).toHaveProperty('average_latency_ms')
    expect(Array.isArray(analytics.maps_created_per_day)).toBe(true)
  })

  it('provides admin audit logs with timestamps and target user IDs', async () => {
    const logs = await adminService.getAuditLogs()
    expect(Array.isArray(logs)).toBe(true)
    expect(logs.length).toBeGreaterThan(0)

    logs.forEach((log) => {
      expect(log).toHaveProperty('action')
      expect(log).toHaveProperty('target_user_id')
      expect(log).toHaveProperty('admin_id')
      expect(log).toHaveProperty('timestamp')
    })
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
