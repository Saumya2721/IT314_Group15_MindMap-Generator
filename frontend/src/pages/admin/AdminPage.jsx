import React, { useState, useEffect, useCallback } from 'react'
import { adminService } from '../../services/adminService'
import { AppHeader } from '../../components/layout/AppHeader'
import { AppFooter } from '../../components/layout/AppFooter'
import { AdminErrorBoundary } from './AdminErrorBoundary'
import './AdminPage.css'

export function AdminDashboardContent() {
  const [users, setUsers] = useState([])
  const [analytics, setAnalytics] = useState(null)
  const [auditLogs, setAuditLogs] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('users') // 'users' | 'audit'

  // Modal State for user action confirmations
  const [confirmModal, setConfirmModal] = useState({
    isOpen: false,
    type: null, // 'suspend' | 'unsuspend' | 'delete'
    user: null,
  })

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [usersData, analyticsData, logsData] = await Promise.all([
        adminService.getUsers(),
        adminService.getAnalytics(),
        adminService.getAuditLogs(),
      ])
      setUsers(Array.isArray(usersData) ? usersData : [])
      setAnalytics(analyticsData)
      setAuditLogs(Array.isArray(logsData) ? logsData : [])
    } catch (err) {
      console.error('Failed to load admin dashboard data:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleOpenConfirm = (user, type) => {
    setConfirmModal({
      isOpen: true,
      type,
      user,
    })
  }

  const handleCloseConfirm = () => {
    setConfirmModal({ isOpen: false, type: null, user: null })
  }

  const handleConfirmAction = async () => {
    const { user, type } = confirmModal
    if (!user || !type) return

    try {
      if (type === 'suspend') {
        await adminService.setUserStatus(user.id, 'suspended')
        setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, status: 'suspended' } : u)))
        const newLog = {
          id: `log-${Date.now()}`,
          action: 'USER_SUSPENDED',
          target_user_id: user.id,
          admin_id: 'CURRENT_ADMIN',
          timestamp: new Date().toISOString(),
          details: `Suspended account for user ${user.email}`,
        }
        setAuditLogs((prev) => [newLog, ...prev])
      } else if (type === 'unsuspend') {
        await adminService.setUserStatus(user.id, 'active')
        setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, status: 'active' } : u)))
        const newLog = {
          id: `log-${Date.now()}`,
          action: 'USER_UNSUSPENDED',
          target_user_id: user.id,
          admin_id: 'CURRENT_ADMIN',
          timestamp: new Date().toISOString(),
          details: `Restored active status for user ${user.email}`,
        }
        setAuditLogs((prev) => [newLog, ...prev])
      } else if (type === 'delete') {
        await adminService.deleteUser(user.id)
        setUsers((prev) => prev.filter((u) => u.id !== user.id))
        const newLog = {
          id: `log-${Date.now()}`,
          action: 'USER_DELETED',
          target_user_id: user.id,
          admin_id: 'CURRENT_ADMIN',
          timestamp: new Date().toISOString(),
          details: `Permanently deleted account ${user.email}`,
        }
        setAuditLogs((prev) => [newLog, ...prev])
      }
    } catch (err) {
      console.error('Admin action execution failed:', err)
    } finally {
      handleCloseConfirm()
    }
  }

  // Calculate highest map count for simple SVG sparkline scaling
  const maxMapCount = analytics?.maps_created_per_day
    ? Math.max(...analytics.maps_created_per_day.map((d) => d.count), 1)
    : 1

  return (
    <div className="admin-layout">
      <AppHeader />
      <main className="admin-main">
        {/* Header & Page Title */}
        <div className="admin-header">
          <span className="admin-badge">
            <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
              shield_person
            </span>
            Platform Administration
          </span>
          <h1 className="admin-title">Management &amp; Analytics</h1>
          <p className="admin-subtitle">
            System health metrics, user governance, and action audit logs. Strict zero-access
            privacy policy guarantees node/edge content is strictly confidential.
          </p>
        </div>

        {/* Aggregate Analytics Widgets */}
        <section className="admin-analytics-grid" aria-label="Platform Analytics">
          {/* Widget 1: Maps Created Per Day */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Maps Created Per Day</span>
              <span className="material-symbols-outlined analytics-card-icon">timeline</span>
            </div>
            <div className="analytics-card-value">
              {analytics?.maps_created_per_day?.reduce((acc, curr) => acc + curr.count, 0) || 0}
            </div>
            <span className="analytics-card-footnote">Last 7 days total</span>

            {/* Sparkline Visualisation */}
            {analytics?.maps_created_per_day && (
              <div className="analytics-chart-wrap" aria-label="Daily creation trend">
                {analytics.maps_created_per_day.map((item) => {
                  const barHeight = Math.round((item.count / maxMapCount) * 40)
                  return (
                    <div
                      key={item.date}
                      className="chart-bar-col"
                      title={`${item.date}: ${item.count} maps`}
                    >
                      <div className="chart-bar-fill" style={{ height: `${barHeight}px` }} />
                      <span className="chart-bar-label">
                        {item.date.split(' ')[1] || item.date}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Widget 2: Platform Failure Rate */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Generation Failure Rate</span>
              <span
                className="material-symbols-outlined analytics-card-icon"
                style={{ color: 'var(--accent-terracotta, #c96b4b)' }}
              >
                report_problem
              </span>
            </div>
            <div className="analytics-card-value">
              {analytics?.failure_rate !== undefined ? `${analytics.failure_rate}%` : '0%'}
            </div>
            <span className="analytics-card-footnote">LLM synthesis error boundary triggers</span>
          </div>

          {/* Widget 3: Average Latency */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Avg. Generation Latency</span>
              <span className="material-symbols-outlined analytics-card-icon">speed</span>
            </div>
            <div className="analytics-card-value">
              {analytics?.average_latency_ms
                ? `${(analytics.average_latency_ms / 1000).toFixed(2)}s`
                : '1.20s'}
            </div>
            <span className="analytics-card-footnote">End-to-end knowledge graph synthesis</span>
          </div>
        </section>

        {/* Tab Toggle Navigation */}
        <div className="admin-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'users'}
            className={`admin-tab-btn ${activeTab === 'users' ? 'active' : ''}`}
            onClick={() => setActiveTab('users')}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              group
            </span>
            Users Directory ({users.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'audit'}
            className={`admin-tab-btn ${activeTab === 'audit' ? 'active' : ''}`}
            onClick={() => setActiveTab('audit')}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              receipt_long
            </span>
            Audit Logs ({auditLogs.length})
          </button>
        </div>

        {/* Tab Content 1: User Management Table */}
        {activeTab === 'users' && (
          <section aria-label="User Accounts Management">
            <div className="admin-table-container">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>User / Account</th>
                    <th>Signed Up</th>
                    <th>Status</th>
                    <th>Total Maps</th>
                    <th>Content Access</th>
                    <th style={{ textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr>
                      <td colSpan="6" style={{ textAlign: 'center', padding: '32px' }}>
                        Loading user directory...
                      </td>
                    </tr>
                  ) : users.length === 0 ? (
                    <tr>
                      <td colSpan="6" style={{ textAlign: 'center', padding: '32px' }}>
                        No user accounts registered.
                      </td>
                    </tr>
                  ) : (
                    users.map((u) => (
                      <tr key={u.id}>
                        <td>
                          <div className="user-info-cell">
                            <span className="user-name-text">{u.name || 'Anonymous User'}</span>
                            <span className="user-email-text">{u.email}</span>
                          </div>
                        </td>
                        <td>
                          {u.signup_date
                            ? new Date(u.signup_date).toLocaleDateString(undefined, {
                                year: 'numeric',
                                month: 'short',
                                day: 'numeric',
                              })
                            : 'N/A'}
                        </td>
                        <td>
                          <span className={`status-badge ${u.status || 'active'}`}>
                            {u.status || 'active'}
                          </span>
                        </td>
                        <td>
                          <strong>{u.total_maps_count ?? 0}</strong>
                        </td>
                        <td>
                          <span
                            className="privacy-notice-badge"
                            title="Zero-knowledge privacy guarantee"
                          >
                            <span
                              className="material-symbols-outlined"
                              style={{ fontSize: '14px' }}
                            >
                              lock
                            </span>
                            Strictly Restricted
                          </span>
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div
                            className="admin-table-actions"
                            style={{ justifyContent: 'flex-end' }}
                          >
                            {u.status === 'suspended' ? (
                              <button
                                type="button"
                                className="btn-admin-action unsuspend"
                                onClick={() => handleOpenConfirm(u, 'unsuspend')}
                                title="Restore user access"
                              >
                                <span
                                  className="material-symbols-outlined"
                                  style={{ fontSize: '15px' }}
                                >
                                  check_circle
                                </span>
                                Unsuspend
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn-admin-action suspend"
                                onClick={() => handleOpenConfirm(u, 'suspend')}
                                title="Suspend user access"
                              >
                                <span
                                  className="material-symbols-outlined"
                                  style={{ fontSize: '15px' }}
                                >
                                  block
                                </span>
                                Suspend
                              </button>
                            )}
                            <button
                              type="button"
                              className="btn-admin-action delete"
                              onClick={() => handleOpenConfirm(u, 'delete')}
                              title="Permanently remove account"
                            >
                              <span
                                className="material-symbols-outlined"
                                style={{ fontSize: '15px' }}
                              >
                                delete
                              </span>
                              Delete
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Tab Content 2: Audit Logs */}
        {activeTab === 'audit' && (
          <section aria-label="Administrative Audit Logs">
            <div className="audit-log-stream">
              {auditLogs.length === 0 ? (
                <div
                  style={{ textAlign: 'center', padding: '40px', color: 'var(--ink-secondary)' }}
                >
                  No administrative actions logged yet.
                </div>
              ) : (
                auditLogs.map((log) => (
                  <div key={log.id} className="audit-log-card">
                    <div className="audit-log-top">
                      <span className="audit-log-action">{log.action}</span>
                      <span className="audit-log-time">
                        {log.timestamp ? new Date(log.timestamp).toLocaleString() : 'Recent'}
                      </span>
                    </div>
                    <p className="audit-log-detail">{log.details}</p>
                    <div className="audit-log-meta">
                      <span>
                        Target User: <strong>{log.target_user_id}</strong>
                      </span>
                      <span>
                        Admin ID: <strong>{log.admin_id}</strong>
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>
        )}

        {/* Confirmation Modal for Suspend/Unsuspend/Delete Actions */}
        {confirmModal.isOpen && (
          <div className="admin-modal-backdrop" onClick={handleCloseConfirm}>
            <div className="admin-modal-dialog" onClick={(e) => e.stopPropagation()}>
              <div className="admin-modal-header">
                <span
                  className="material-symbols-outlined"
                  style={{
                    fontSize: '24px',
                    color: confirmModal.type === 'delete' ? '#ba1a1a' : 'var(--accent-terracotta)',
                  }}
                >
                  {confirmModal.type === 'delete' ? 'delete_forever' : 'warning'}
                </span>
                <h3 className="admin-modal-title">
                  {confirmModal.type === 'delete'
                    ? 'Confirm Account Deletion'
                    : confirmModal.type === 'suspend'
                      ? 'Confirm Account Suspension'
                      : 'Confirm Account Reactivation'}
                </h3>
              </div>
              <p className="admin-modal-body">
                {confirmModal.type === 'delete' ? (
                  <>
                    Are you sure you want to permanently delete user account{' '}
                    <strong>{confirmModal.user?.email}</strong>? All associated account metadata
                    will be wiped. This action cannot be undone.
                  </>
                ) : confirmModal.type === 'suspend' ? (
                  <>
                    Are you sure you want to suspend user account{' '}
                    <strong>{confirmModal.user?.email}</strong>? The user will be prevented from
                    authenticating or generating new mind maps.
                  </>
                ) : (
                  <>
                    Restore full platform access for user{' '}
                    <strong>{confirmModal.user?.email}</strong>?
                  </>
                )}
              </p>
              <div className="admin-modal-actions">
                <button type="button" className="btn-modal-cancel" onClick={handleCloseConfirm}>
                  Cancel
                </button>
                <button
                  type="button"
                  className={
                    confirmModal.type === 'delete'
                      ? 'btn-modal-confirm-danger'
                      : 'btn-modal-confirm-warning'
                  }
                  onClick={handleConfirmAction}
                >
                  {confirmModal.type === 'delete'
                    ? 'Delete Account'
                    : confirmModal.type === 'suspend'
                      ? 'Suspend User'
                      : 'Unsuspend User'}
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
      <AppFooter />
    </div>
  )
}

export default function AdminPage() {
  return (
    <AdminErrorBoundary>
      <AdminDashboardContent />
    </AdminErrorBoundary>
  )
}
