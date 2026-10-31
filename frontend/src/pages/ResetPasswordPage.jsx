import React, { useState, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { authService } from '../services/authService'
import './LoginPage.css'

export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const location = useLocation()

  const [token, setToken] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const initToken = async () => {
      // 1. Supabase implicit flow redirects with hash fragment: #access_token=...&type=recovery
      let accessToken = ''
      const hash = window.location.hash
      if (hash) {
        const params = new URLSearchParams(hash.replace(/^#/, ''))
        accessToken = params.get('access_token') || ''
      }

      // 2. Also check standard query parameters as fallback
      const searchParams = new URLSearchParams(location.search)
      if (!accessToken) {
        accessToken = searchParams.get('token') || searchParams.get('access_token') || ''
      }

      // 3. PKCE authorization code flow: ?code=...
      const code = searchParams.get('code')
      if (!accessToken && code) {
        setLoading(true)
        try {
          const res = await authService.exchangeGoogleCode(code)
          if (res.success && res.token) {
            accessToken = res.token
          } else {
            setError(res.error || 'Failed to exchange recovery code.')
          }
        } catch (e) {
          setError('Could not verify recovery link.')
        } finally {
          setLoading(false)
        }
      }

      if (accessToken) {
        setToken(accessToken)
        setError('')
      } else if (!code) {
        setError('No recovery token found. Please re-open the link from your email.')
      }
    }

    initToken()
  }, [location])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (!token) {
      setError('Missing recovery token. Please request a new password reset email.')
      return
    }

    if (!password) {
      setError('Please enter a new password.')
      return
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters.')
      return
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      const result = await authService.resetPassword(token, password)
      if (result.success) {
        setSuccess('Your password has been successfully reset! Redirecting to sign in...')
        setTimeout(() => {
          navigate('/login')
        }, 2000)
      } else {
        setError(result.error || 'Failed to reset password. The link may have expired.')
      }
    } catch (err) {
      setError('An unexpected error occurred. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="auth-container">
      {/* Left Column: Atelier Showcase */}
      <div className="atelier-showcase">
        <div className="dot-grid-overlay" />
        <div className="brand-bar">
          <div className="brand-logo">
            <span className="material-symbols-outlined" style={{ fontSize: '28px' }}>
              hub
            </span>
          </div>
          <div>
            <h1 className="brand-title">MindWeave</h1>
            <p className="brand-subtitle">Knowledge Graph Synthesis Atelier</p>
          </div>
        </div>

        <div className="showcase-content">
          <div className="conceptual-badge">
            <span className="badge-dot" />
            <span className="badge-text">Account Security</span>
          </div>
          <h2 className="showcase-headline">
            Restore access to your <em>cognitive workspace</em>.
          </h2>
          <p className="showcase-body">
            Choose a new strong passphrase to secure your topological mind maps and conceptual
            archives.
          </p>
        </div>

        <div className="showcase-footer">
          <span>MindWeave Research Studio v0.1</span>
        </div>
      </div>

      {/* Right Column: Reset Password Form */}
      <div className="auth-portal">
        <div className="portal-inner">
          <div className="portal-header">
            <h2 className="portal-title">Reset Passphrase</h2>
            <p className="portal-desc">Enter and confirm your new account password below.</p>
          </div>

          {error && (
            <div className="alert-banner alert-error" role="alert">
              <span className="material-symbols-outlined alert-icon">error</span>
              <span>{error}</span>
            </div>
          )}

          {success && (
            <div className="alert-banner alert-success" role="alert">
              <span className="material-symbols-outlined alert-icon">check_circle</span>
              <span>{success}</span>
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            {/* New Password */}
            <div className="form-field">
              <label className="field-label" htmlFor="new-password">
                New Password
              </label>
              <div className="input-wrapper">
                <span className="material-symbols-outlined input-icon">lock</span>
                <input
                  id="new-password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  className="field-input"
                  placeholder="Enter new passphrase (min. 6 characters)"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={{ paddingRight: '40px' }}
                />
                <button
                  type="button"
                  className="input-toggle-btn"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                    {showPassword ? 'visibility' : 'visibility_off'}
                  </span>
                </button>
              </div>
            </div>

            {/* Confirm Password */}
            <div className="form-field">
              <label className="field-label" htmlFor="confirm-password">
                Confirm New Password
              </label>
              <div className="input-wrapper">
                <span className="material-symbols-outlined input-icon">lock_reset</span>
                <input
                  id="confirm-password"
                  name="confirmPassword"
                  type={showConfirmPassword ? 'text' : 'password'}
                  className="field-input"
                  placeholder="Confirm new passphrase"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  style={{ paddingRight: '40px' }}
                />
                <button
                  type="button"
                  className="input-toggle-btn"
                  onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                  aria-label={showConfirmPassword ? 'Hide password' : 'Show password'}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                    {showConfirmPassword ? 'visibility' : 'visibility_off'}
                  </span>
                </button>
              </div>
            </div>

            <button type="submit" className="submit-btn" disabled={loading || !token}>
              {loading ? 'Updating Passphrase...' : 'Update Password & Proceed'}
            </button>

            <div style={{ textAlign: 'center', marginTop: '1.5rem' }}>
              <button
                type="button"
                className="field-link"
                onClick={() => navigate('/login')}
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
              >
                ← Return to Sign In
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
