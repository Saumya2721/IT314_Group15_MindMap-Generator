import { describe, it, expect, vi, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AdminDashboardContent } from '../pages/admin/AdminPage'
import { AdminRoute } from '../App'
import { adminService } from '../services/adminService'

// Mock adminService
vi.mock('../services/adminService', () => ({
  adminService: {
    getUsers: vi.fn(),
    getAnalytics: vi.fn(),
    getAuditLogs: vi.fn(),
    setUserStatus: vi.fn(),
    deleteUser: vi.fn(),
  },
}))

// Mock AppHeader and AppFooter
vi.mock('../components/layout/AppHeader', () => ({
  AppHeader: () => <header data-testid="app-header">Header</header>,
}))
vi.mock('../components/layout/AppFooter', () => ({
  AppFooter: () => <footer data-testid="app-footer">Footer</footer>,
}))

// Mock useAuth for testing AdminRoute
let mockAuthUser = null
let mockIsInitializing = false

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: mockAuthUser,
    isInitializing: mockIsInitializing,
  }),
}))

describe('S3-FE1: Admin Route Guard', () => {
  beforeEach(() => {
    mockAuthUser = null
    mockIsInitializing = false
  })

  it('redirects unauthenticated users away from /admin', () => {
    mockAuthUser = null
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminRoute>
          <div data-testid="admin-panel-content">Secret Content</div>
        </AdminRoute>
      </MemoryRouter>,
    )

    expect(screen.queryByTestId('admin-panel-content')).toBeNull()
  })

  it('redirects standard non-admin users (role === "user") to dashboard', () => {
    mockAuthUser = { id: 'usr-1', email: 'user@test.com', role: 'user' }
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminRoute>
          <div data-testid="admin-panel-content">Secret Content</div>
        </AdminRoute>
      </MemoryRouter>,
    )

    expect(screen.queryByTestId('admin-panel-content')).toBeNull()
  })

  it('grants full access when user role is admin', () => {
    mockAuthUser = { id: 'admin-1', email: 'admin@mindweave.craft', role: 'admin' }
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminRoute>
          <div data-testid="admin-panel-content">Secret Content</div>
        </AdminRoute>
      </MemoryRouter>,
    )

    expect(screen.getByTestId('admin-panel-content')).toBeDefined()
  })
})

describe('S3-FE1: Admin Dashboard UI & Actions', () => {
  const sampleUsers = [
    {
      id: 'usr-1',
      name: 'Alice Curator',
      email: 'alice@mindweave.craft',
      role: 'user',
      status: 'active',
      signup_date: '2026-11-01T00:00:00Z',
      total_maps_count: 7,
    },
    {
      id: 'usr-2',
      name: 'Bob Spammer',
      email: 'bob@spam.io',
      role: 'user',
      status: 'suspended',
      signup_date: '2026-11-05T00:00:00Z',
      total_maps_count: 0,
    },
  ]

  const sampleAnalytics = {
    maps_created_per_day: [
      { date: 'Nov 19', count: 10 },
      { date: 'Nov 20', count: 25 },
    ],
    failure_rate: 2.4,
    average_latency_ms: 1350,
  }

  const sampleLogs = [
    {
      id: 'log-1',
      action: 'USER_SUSPENDED',
      target_user_id: 'usr-2',
      admin_id: 'admin-1',
      timestamp: '2026-11-20T10:00:00Z',
      details: 'Suspended suspicious account.',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
    adminService.getUsers.mockResolvedValue(sampleUsers)
    adminService.getAnalytics.mockResolvedValue(sampleAnalytics)
    adminService.getAuditLogs.mockResolvedValue(sampleLogs)
  })

  it('renders analytics metrics correctly', async () => {
    render(
      <MemoryRouter>
        <AdminDashboardContent />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('Generation Failure Rate')).toBeDefined()
    })

    expect(screen.getByText('2.4%')).toBeDefined()
    expect(screen.getByText('1.35s')).toBeDefined()
    expect(screen.getByText('35')).toBeDefined() // 10 + 25
  })

  it('renders user table with privacy guarantees (no map content)', async () => {
    render(
      <MemoryRouter>
        <AdminDashboardContent />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('alice@mindweave.craft')).toBeDefined()
      expect(screen.getByText('bob@spam.io')).toBeDefined()
    })

    // Confirm privacy badges are present
    const privacyBadges = screen.getAllByText('Strictly Restricted')
    expect(privacyBadges.length).toBe(2)
  })

  it('opens confirmation modal and executes suspend action', async () => {
    adminService.setUserStatus.mockResolvedValue({ success: true })

    render(
      <MemoryRouter>
        <AdminDashboardContent />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('alice@mindweave.craft')).toBeDefined()
    })

    const suspendBtn = screen.getByTitle('Suspend user access')
    fireEvent.click(suspendBtn)

    // Modal dialog is shown
    expect(screen.getByText('Confirm Account Suspension')).toBeDefined()

    const confirmBtn = screen.getByText('Suspend User')
    fireEvent.click(confirmBtn)

    await waitFor(() => {
      expect(adminService.setUserStatus).toHaveBeenCalledWith('usr-1', 'suspended')
    })
  })

  it('switches to audit log view and displays logged operations', async () => {
    render(
      <MemoryRouter>
        <AdminDashboardContent />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/Audit Logs/i)).toBeDefined()
    })

    const auditTabBtn = screen.getByRole('tab', { name: /Audit Logs/i })
    fireEvent.click(auditTabBtn)

    expect(screen.getByText('USER_SUSPENDED')).toBeDefined()
    expect(screen.getByText('Suspended suspicious account.')).toBeDefined()
  })
})
