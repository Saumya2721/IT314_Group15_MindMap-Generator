import { apiClient } from './apiClient'

function requireData(response, message) {
  if (!response?.ok || response.data == null || response.data.success === false) {
    throw new Error(response?.error || response?.data?.message || message)
  }
  return response.data
}

export const adminService = {
  async getUsers(limit = 20, offset = 0) {
    return requireData(
      await apiClient.get(`/api/admin/users?limit=${limit}&offset=${offset}`),
      'Could not load the user directory.',
    )
  },

  async setUserStatus(userId, status) {
    const action = status === 'suspended' ? 'suspend' : 'unsuspend'
    return requireData(
      await apiClient.post(`/api/admin/users/${encodeURIComponent(userId)}/${action}`),
      'Could not change account status.',
    )
  },

  async deleteUser(userId) {
    return requireData(
      await apiClient.delete(`/api/admin/users/${encodeURIComponent(userId)}`),
      'Account deletion did not complete.',
    )
  },

  async getAnalytics() {
    return requireData(
      await apiClient.get('/api/admin/stats'),
      'Could not load platform analytics.',
    )
  },

  async getAuditLogs() {
    return requireData(await apiClient.get('/api/admin/audit-log'), 'Could not load the audit log.')
  },

  async getPipelineConfig() {
    return requireData(
      await apiClient.get('/api/admin/config'),
      'Could not load pipeline settings.',
    )
  },

  async updatePipelineConfig(updates) {
    const payload = Object.fromEntries(
      Object.entries(updates).map(([key, value]) => [key.toLowerCase(), value]),
    )
    return requireData(
      await apiClient.patch('/api/admin/config', payload),
      'Could not save pipeline settings.',
    )
  },
}
