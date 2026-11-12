import React, { useState, useRef, useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import { getUserAvatar } from '../../utils/formatters'
import './AppHeader.css'

function getSafeAvatarUrl(user) {
  return getUserAvatar(user)
}

export function AppHeader() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef(null)

  // Close dropdown if clicked outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleLogout = async () => {
    setDropdownOpen(false)
    await logout()
    navigate('/login')
  }

  return (
    <header className="app-header">
      <div className="app-header-inner">
        {/* Left: Branding & Navigation */}
        <div className="app-header-left">
          <Link to="/dashboard" className="header-brand-link">
            <span className="header-brand-monogram">M</span>
            <span className="header-brand-text">MINDWEAVE</span>
          </Link>

          <nav className="header-nav">
            <Link
              to="/dashboard"
              className={`header-nav-link ${location.pathname === '/dashboard' ? 'active' : ''}`}
            >
              Dashboard
            </Link>
            <Link
              to="/dashboard"
              className="header-nav-link"
              onClick={(e) => {
                e.preventDefault()
                alert('My Maps view is available on the Dashboard in Sprint 1.')
              }}
            >
              My Maps
            </Link>
            <Link
              to="/dashboard"
              className="header-nav-link"
              onClick={(e) => {
                e.preventDefault()
                alert('Archive feature will be connected in future sprints.')
              }}
            >
              Archive
            </Link>
          </nav>
        </div>

        {/* Right: Primary Action & Profile */}
        <div className="app-header-right">
          <Link to="/create" className="btn-header-new-map">
            <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
              add
            </span>
            <span>New Map</span>
          </Link>

          {/* User Profile Avatar with Logout Dropdown */}
          <div className="header-profile-menu-wrapper" ref={dropdownRef}>
            <button
              type="button"
              className="header-avatar-btn"
              onClick={() => setDropdownOpen(!dropdownOpen)}
              aria-label="User account menu"
            >
              <img src={getSafeAvatarUrl(user)} alt="Profile" className="header-avatar-img" />
            </button>

            {dropdownOpen && (
              <div className="profile-dropdown">
                <div className="profile-dropdown-user">
                  <div className="dropdown-user-name">{user?.name || 'Curator'}</div>
                  <div className="dropdown-user-email">
                    {user?.email || 'curator@mindweave.craft'}
                  </div>
                </div>
                <button type="button" className="dropdown-logout-btn" onClick={handleLogout}>
                  <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                    logout
                  </span>
                  <span>Log Out</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
