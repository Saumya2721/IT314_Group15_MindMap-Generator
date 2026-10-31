import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { authService } from '../services/authService'

/**
 * OAuthCallbackPage
 *
 * Handles the redirect from Google OAuth via Supabase.
 * Supabase redirects the browser to FRONTEND_URL/auth/callback with:
 *   - ?code=<authorization_code>   (success)
 *   - ?error=<error_code>&error_description=<desc>  (failure)
 *
 * Flow:
 *  1. Read code/error from URL search params
 *  2. POST code to /auth/google/callback → backend exchanges code, sets HttpOnly cookies
 *  3. Call /me to hydrate user in AuthContext
 *  4. Navigate to intended destination (or /dashboard)
 */
export default function OAuthCallbackPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { hydrateFromOAuth } = useAuth()

  const [status, setStatus] = useState('exchanging') // 'exchanging' | 'error'
  const [errorMessage, setErrorMessage] = useState('')
  const hasRun = useRef(false)

  useEffect(() => {
    // Guard against React StrictMode double-invocation
    if (hasRun.current) return
    hasRun.current = true

    const handleCallback = async () => {
      const params = new URLSearchParams(location.search)
      const code = params.get('code')
      const oauthError = params.get('error')
      const oauthErrorDesc = params.get('error_description')

      // Handle OAuth error returned by Google/Supabase
      if (oauthError) {
        const desc = oauthErrorDesc
          ? decodeURIComponent(oauthErrorDesc.replace(/\+/g, ' '))
          : oauthError
        setErrorMessage(`Google sign-in was denied or failed: ${desc}`)
        setStatus('error')
        return
      }

      // No code present — likely a direct page load or invalid redirect
      if (!code) {
        setErrorMessage('No authorization code received. Please try signing in again.')
        setStatus('error')
        return
      }

      // Exchange the authorization code for a session
      const exchangeRes = await authService.exchangeGoogleCode(code)
      if (!exchangeRes.success) {
        setErrorMessage(exchangeRes.error || 'Google sign-in failed. Please try again.')
        setStatus('error')
        return
      }

      // Hydrate the auth context from the newly set HttpOnly cookies
      const hydrateRes = await hydrateFromOAuth()
      if (!hydrateRes.success) {
        setErrorMessage(hydrateRes.error || 'Could not restore session. Please try again.')
        setStatus('error')
        return
      }

      // Retrieve and sanitize intended destination from application storage
      // Do not trust protocol OAuth 'state' parameter as arbitrary navigation URL
      let intendedPath = '/dashboard'
      try {
        const storedPath = sessionStorage.getItem('mindweave_oauth_return_to')
        if (storedPath) {
          sessionStorage.removeItem('mindweave_oauth_return_to')
          // Validate same-origin relative path to prevent open redirect vulnerabilities
          if (
            storedPath.startsWith('/') &&
            !storedPath.startsWith('//') &&
            !storedPath.includes('://')
          ) {
            intendedPath = storedPath
          }
        }
      } catch (e) {
        console.warn('Could not read oauth return path:', e)
      }

      navigate(intendedPath, { replace: true })
    }

    handleCallback()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  if (status === 'error') {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          backgroundColor: 'var(--surface)',
          gap: '16px',
          padding: '32px',
          textAlign: 'center',
        }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '40px', color: 'var(--error, #c62828)' }}
        >
          error_outline
        </span>
        <h2
          style={{
            fontFamily: 'var(--font-headline)',
            color: 'var(--on-surface)',
            fontSize: '20px',
            margin: 0,
          }}
        >
          Google Sign-In Failed
        </h2>
        <p
          style={{
            color: 'var(--outline)',
            fontSize: '14px',
            maxWidth: '420px',
            margin: 0,
          }}
        >
          {errorMessage}
        </p>
        <button
          onClick={() => navigate('/login', { replace: true })}
          style={{
            marginTop: '8px',
            padding: '10px 24px',
            backgroundColor: 'var(--primary)',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            fontSize: '14px',
            fontFamily: 'var(--font-body)',
          }}
        >
          Back to sign in
        </button>
      </div>
    )
  }

  // Exchanging / loading state
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        backgroundColor: 'var(--surface)',
        gap: '12px',
      }}
    >
      <span
        className="material-symbols-outlined step-spin-icon"
        style={{ fontSize: '32px', color: 'var(--primary)' }}
      >
        sync
      </span>
      <p
        style={{
          color: 'var(--outline)',
          fontFamily: 'var(--font-headline)',
          fontSize: '15px',
          margin: 0,
        }}
      >
        Completing Google sign-in...
      </p>
    </div>
  )
}
