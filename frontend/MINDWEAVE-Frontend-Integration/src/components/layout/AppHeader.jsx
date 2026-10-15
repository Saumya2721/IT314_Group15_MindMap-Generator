import React, { useState, useRef, useEffect } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import './AppHeader.css';

const DEFAULT_AVATAR_URL =
  'https://lh3.googleusercontent.com/aida-public/AB6AXuB5NFQwx9Y0rkms3wT-alCi2zxtqHCZTAyMjKmP-sM-J9ndHLFRGNXSNIIJoy3udUkvYoWpJx3UparXK04g0--C0qgINk6I23Gr51wKOCjjTxhG6omD1AYLepqxSr4wUeUe39GJz6LhROot39TPah3fin69PBU9KXsoLCY2Fo2FO1NCISm6AXUrQyU6lR4KkwE-b4sTXAkCvVxebm23xefPgmr7PxjecBiZeQ2FpHHdTOOgkS9HQo87';

function getSafeAvatarUrl(avatar) {
  if (typeof avatar !== 'string' || !avatar.trim()) {
    return DEFAULT_AVATAR_URL;
  }

  try {
    const parsed = new URL(avatar, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.href;
    }
  } catch {
    // ignore invalid URL and use fallback
  }

  return DEFAULT_AVATAR_URL;
}

export function AppHeader() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown if clicked outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLogout = async () => {
    setDropdownOpen(false);
    await logout();
    navigate('/login');
  };

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
                e.preventDefault();
                alert('My Maps view is available on the Dashboard in Sprint 1.');
              }}
            >
              My Maps
            </Link>
            <Link
              to="/dashboard"
              className="header-nav-link"
              onClick={(e) => {
                e.preventDefault();
                alert('Archive feature will be connected in future sprints.');
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
              <img
                src={getSafeAvatarUrl(user?.avatar)}
                alt="Profile"
                className="header-avatar-img"
              />
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
  );
}
