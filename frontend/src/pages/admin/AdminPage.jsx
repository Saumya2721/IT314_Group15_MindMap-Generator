import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
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
  const [actionError, setActionError] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [userOffset, setUserOffset] = useState(0)

  // Modal State for user action confirmations
  const [confirmModal, setConfirmModal] = useState({
    isOpen: false,
    type: null, // 'suspend' | 'unsuspend' | 'delete'
    user: null,
  })

  const loadData = useCallback(async () => {
    setLoading(true)
    const [directory, stats, logs] = await Promise.allSettled([
      adminService.getUsers(20, userOffset),
      adminService.getAnalytics(),
      adminService.getAuditLogs(),
    ])
    setUsers(directory.status === 'fulfilled' ? directory.value : [])
    setAnalytics(stats.status === 'fulfilled' ? stats.value : null)
    setAuditLogs(logs.status === 'fulfilled' ? logs.value : [])
    setLoadError(
      [directory, stats, logs]
        .filter((result) => result.status === 'rejected')
        .map((result) => result.reason.message)
        .join(' '),
    )
    setLoading(false)
  }, [userOffset])

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
    setActionError(null)

    try {
      if (type === 'delete') {
        await adminService.deleteUser(user.id)
      } else {
        await adminService.setUserStatus(user.id, type === 'suspend' ? 'suspended' : 'active')
      }
      await loadData()
    } catch (err) {
      console.error('Admin action execution failed:', err)
      setActionError(err?.message || 'Admin action execution failed.')
      if (type === 'delete') await loadData()
    } finally {
      handleCloseConfirm()
    }
  }

  return (
    <div className="admin-layout">
      <AppHeader />
      <main className="admin-main">
        {/* Header & Page Title */}
        <div className="admin-header">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="admin-badge">
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>
                shield_person
              </span>
              Platform Administration
            </span>
            <Link
              to="/admin/config"
              className="admin-badge"
              style={{
                textDecoration: 'none',
                backgroundColor: 'rgba(45, 90, 71, 0.2)',
                color: 'var(--accent-sage, #899b72)',
                border: '1px solid rgba(137, 155, 114, 0.3)',
                fontWeight: 600,
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                cursor: 'pointer',
              }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                tune
              </span>
              Pipeline Runtime Settings
            </Link>
          </div>

          <div className="admin-subnav-tabs">
            <Link to="/admin" className="admin-subnav-tab active">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                dashboard
              </span>
              Overview &amp; Analytics
            </Link>
            <Link to="/admin/config" className="admin-subnav-tab">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                tune
              </span>
              Pipeline Runtime Settings
            </Link>
          </div>

          <h1 className="admin-title">Management &amp; Analytics</h1>
          <p className="admin-subtitle">
            System health metrics, user governance, and action audit logs. Strict zero-access
            privacy policy guarantees node/edge content is strictly confidential.
          </p>
        </div>

        {(actionError || loadError) && (
          <div
            className="admin-error-banner"
            role="alert"
            style={{
              margin: '0 0 24px 0',
              padding: '14px 18px',
              backgroundColor: '#fee2e2',
              border: '1px solid #ef4444',
              borderRadius: '8px',
              color: '#b91c1c',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              fontSize: '14px',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>
              error
            </span>
            <span>{actionError || loadError}</span>
          </div>
        )}

        {/* Aggregate Analytics Widgets */}
        <section className="admin-analytics-grid" aria-label="Platform Analytics">
          {/* Widget 1: Maps Generated */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Maps Generated</span>
              <span className="material-symbols-outlined analytics-card-icon">timeline</span>
            </div>
            <div className="analytics-card-value">
              {analytics?.map_generation?.maps_generated_last_7_days ?? 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">Last 7 days total</span>
            {analytics?.map_generation?.maps_created_per_day?.length > 0 && (
              <ol className="analytics-chart-wrap" aria-label="Daily generation counts, UTC">
                {analytics.map_generation.maps_created_per_day.map(({ date, count }) => (
                  <li key={date} className="chart-bar-col" title={date}>
                    <strong>{count}</strong>
                    <span className="chart-bar-label">{date.slice(5)}</span>
                  </li>
                ))}
              </ol>
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
              {analytics
                ? `${(analytics.job_failures.failure_rate * 100).toFixed(1)}%`
                : 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">LLM synthesis error boundary triggers</span>
          </div>

          {/* Widget 3: Average Generation Latency */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Avg. Generation Latency</span>
              <span className="material-symbols-outlined analytics-card-icon">speed</span>
            </div>
            <div className="analytics-card-value">
              {analytics
                ? `${analytics.performance.avg_generation_latency_seconds.toFixed(2)}s`
                : 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">End-to-end knowledge graph synthesis</span>
          </div>

          {/* Widget 4: Active Users & Platform Reach */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Active Users (7 Days)</span>
              <span
                className="material-symbols-outlined analytics-card-icon"
                style={{ color: 'var(--accent-sage, #899b72)' }}
              >
                group
              </span>
            </div>
            <div className="analytics-card-value">
              {analytics?.users?.active_users_last_7_days ?? 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">
              {analytics?.users
                ? `${analytics.users.total_users} registered users (${analytics.users.active_users_last_30_days} active 30d)`
                : 'Total accounts registered'}
            </span>
          </div>

          {/* Widget 5: Chat Assistant Latency */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Avg. Chat Latency</span>
              <span className="material-symbols-outlined analytics-card-icon">chat_bubble</span>
            </div>
            <div className="analytics-card-value">
              {analytics?.performance?.avg_chat_turn_latency_seconds !== undefined
                ? `${analytics.performance.avg_chat_turn_latency_seconds.toFixed(2)}s`
                : 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">
              Turnaround for conversational RAG queries
            </span>
          </div>

          {/* Widget 6: Completed Jobs */}
          <div className="analytics-card">
            <div className="analytics-card-header">
              <span className="analytics-card-label">Completed Jobs</span>
              <span
                className="material-symbols-outlined analytics-card-icon"
                style={{ color: 'var(--accent-sage, #899b72)' }}
              >
                task_alt
              </span>
            </div>
            <div className="analytics-card-value">
              {analytics?.job_failures?.completed_jobs ?? 'Unavailable'}
            </div>
            <span className="analytics-card-footnote">
              {analytics?.job_failures
                ? `${analytics.job_failures.failed_jobs} failed / ${analytics.map_generation?.total_generations || 0} total requests`
                : 'Autonomous graph synthesis executions'}
            </span>
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
                            <span className="user-name-text">{u.name || u.email || u.id}</span>
                            <span className="user-email-text">{u.email}</span>
                          </div>
                        </td>
                        <td>
                          {u.created_at
                            ? new Date(u.created_at).toLocaleDateString(undefined, {
                                year: 'numeric',
                                month: 'short',
                                day: 'numeric',
                              })
                            : 'N/A'}
                        </td>
                        <td>
                          <span
                            className={`status-badge ${u.status === 'deletion_pending' ? 'Deletion pending' : u.status || 'active'}`}
                          >
                            {u.status || 'active'}
                          </span>
                        </td>
                        <td>
                          <strong>{u.map_count ?? 0}</strong>
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
                            {u.status === 'deletion_pending' ? null : u.status === 'suspended' ? (
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
                              {u.status === 'deletion_pending' ? 'Retry deletion' : 'Delete'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
              <nav aria-label="User directory pages">
                <button
                  type="button"
                  disabled={loading || userOffset === 0}
                  onClick={() => setUserOffset(userOffset - 20)}
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={loading || users.length < 20}
                  onClick={() => setUserOffset(userOffset + 20)}
                >
                  Next
                </button>
              </nav>
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
                        {log.created_at ? new Date(log.created_at).toLocaleString() : 'Recent'}
                      </span>
                    </div>
                    <div className="audit-log-meta">
                      <span>
                        Target User: <strong>{log.target_user_id}</strong>
                      </span>
                      <span>
                        Admin ID: <strong>{log.actor_id}</strong>
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
