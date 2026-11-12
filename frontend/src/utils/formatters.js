/**
 * Formatting and cosmetic utility functions
 */

export function formatRelativeTime(dateString) {
  if (!dateString) return 'Recently'

  try {
    const date = new Date(dateString)
    if (isNaN(date.getTime())) return dateString

    const now = new Date()
    const diffSeconds = Math.floor((now - date) / 1000)

    if (diffSeconds < 60) return 'Just now'
    const diffMinutes = Math.floor(diffSeconds / 60)
    if (diffMinutes < 60) return `${diffMinutes}m ago`
    const diffHours = Math.floor(diffMinutes / 60)
    if (diffHours < 24) return `${diffHours}h ago`
    const diffDays = Math.floor(diffHours / 24)
    if (diffDays === 1) return 'Yesterday'
    if (diffDays < 7) return `${diffDays}d ago`

    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    })
  } catch {
    return 'Recently'
  }
}

export function getUserAvatar(user) {
  if (user?.avatar && typeof user.avatar === 'string' && user.avatar.trim()) {
    return user.avatar
  }
  const name = user?.name || user?.email?.split('@')[0] || 'User'
  return `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=00454a&color=fff&rounded=true&bold=true`
}
