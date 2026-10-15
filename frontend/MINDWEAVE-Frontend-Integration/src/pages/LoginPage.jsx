import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './LoginPage.css';

export default function LoginPage() {
  const navigate = useNavigate();
  const { login, register, loginWithGoogle, loading } = useAuth();

  // Mode state: 'signin' or 'signup'
  const [mode, setMode] = useState('signin');

  // Form input fields
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    password: '',
    confirmPassword: '',
    rememberMe: true,
  });

  // UI state for password visibility toggle
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  // Validation error state
  const [errors, setErrors] = useState({});
  const [successMessage, setSuccessMessage] = useState('');

  // Handle generic input change
  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
    // Clear field-specific error upon typing
    if (errors[name]) {
      setErrors((prev) => ({ ...prev, [name]: '' }));
    }
  };

  // Switch between Sign In and Sign Up modes
  const handleSwitchMode = (targetMode) => {
    setMode(targetMode);
    setErrors({});
    if (targetMode === 'signup') {
      setSuccessMessage('');
      setFormData((prev) => ({
        ...prev,
        name: '',
        confirmPassword: '',
      }));
    }
  };

  // Client-side form validation
  const validateForm = () => {
    const newErrors = {};
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    // Validate email
    if (!formData.email.trim()) {
      newErrors.email = 'Email address is required.';
    } else if (!emailRegex.test(formData.email.trim())) {
      newErrors.email = 'Please enter a valid email address.';
    }

    // Validate password
    if (!formData.password) {
      newErrors.password = 'Password is required.';
    } else if (formData.password.length < 6) {
      newErrors.password = 'Password must be at least 6 characters.';
    }

    // Additional validations for Sign Up
    if (mode === 'signup') {
      if (!formData.name.trim()) {
        newErrors.name = 'Full name is required.';
      }

      if (!formData.confirmPassword) {
        newErrors.confirmPassword = 'Confirm your password.';
      } else if (formData.password !== formData.confirmPassword) {
        newErrors.confirmPassword = 'Passwords do not match.';
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Submit handler
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validateForm()) return;

    if (mode === 'signin') {
      const result = await login(formData.email, formData.password);
      if (result && result.success) {
        navigate('/dashboard');
      } else {
        setErrors({ general: result?.error || 'Authentication failed. Please check credentials.' });
      }
    } else {
      const result = await register(formData.name, formData.email, formData.password);
      if (result && result.success) {
        setSuccessMessage(result?.message || 'Account registered successfully! Please log in.');
        setMode('signin');
      } else {
        setErrors({ general: result?.error || 'Registration failed. Please try again.' });
      }
    }
  };

  // Google federated sign-in handler
  const handleGoogleSignIn = async () => {
    const result = await loginWithGoogle();
    if (result && result.success && result.user) {
      navigate('/dashboard');
    } else if (result && !result.success && result.error) {
      setErrors({ general: result.error });
    }
  };

  return (
    <div className="auth-container">
      {/* =========================================================
          LEFT COLUMN: Editorial Atelier & Conceptual Showcase
          ========================================================= */}
      <div className="atelier-showcase">
        {/* Subtle Architectural Dot Grid Overlay */}
        <div className="dot-grid-overlay" />

        {/* Top Brand Identifier */}
        <div className="brand-bar">
          <div className="brand-meta">
            <span className="brand-monogram">M</span>
            <div className="brand-titles">
              <span className="brand-title">MINDWEAVE</span>
              <span className="brand-subtitle">Cartography of Thought</span>
            </div>
          </div>
          <div className="archival-badge">
            <span className="badge-dot" />
            <span>VOL. IV · ARCHIVAL SUITE</span>
          </div>
        </div>

        {/* Center Editorial Narrative & Mind Map Preview */}
        <div className="showcase-narrative">
          <h1 className="showcase-headline">
            Turn scattered thoughts into something you can{' '}
            <span className="headline-accent">see</span>.
          </h1>

          {/* Connected Mind Map SVG Card */}
          <div className="mindmap-preview-card">
            <div className="preview-card-header">
              <span className="preview-card-title">Topological Thought Map · Draft 04</span>
              <div className="preview-card-palette">
                <span className="palette-dot palette-teal" />
                <span className="palette-dot palette-terracotta" />
                <span className="palette-dot palette-mustard" />
                <span className="palette-dot palette-olive" />
              </div>
            </div>

            <svg
              className="mindmap-svg"
              fill="none"
              viewBox="0 0 540 240"
              xmlns="http://www.w3.org/2000/svg"
            >
              {/* Organic Curved Connectors */}
              <path
                d="M 95 120 C 150 120, 160 55, 230 55"
                opacity="0.75"
                stroke="#155E63"
                strokeDasharray="3 3"
                strokeWidth="1.75"
              />
              <path
                d="M 95 120 C 150 120, 170 120, 230 120"
                opacity="0.85"
                stroke="#155E63"
                strokeWidth="2"
              />
              <path
                d="M 95 120 C 150 120, 160 185, 230 185"
                opacity="0.75"
                stroke="#C96B4B"
                strokeDasharray="3 3"
                strokeWidth="1.75"
              />
              <path
                d="M 330 55 C 375 55, 395 40, 440 40"
                opacity="0.65"
                stroke="#899B72"
                strokeWidth="1.5"
              />
              <path
                d="M 335 120 C 375 120, 395 90, 440 90"
                opacity="0.8"
                stroke="#155E63"
                strokeWidth="1.75"
              />
              <path
                d="M 335 120 C 375 120, 395 150, 440 150"
                opacity="0.8"
                stroke="#D6A84F"
                strokeWidth="1.75"
              />
              <path
                d="M 330 185 C 375 185, 395 200, 440 200"
                opacity="0.65"
                stroke="#C96B4B"
                strokeDasharray="2 2"
                strokeWidth="1.5"
              />

              {/* Anchor Core Node */}
              <g>
                <rect fill="#FFFFFF" height="44" rx="4" stroke="#D0C9BA" strokeWidth="1" width="105" x="20" y="98" />
                <rect fill="#155E63" height="3.5" rx="1.5" width="105" x="20" y="98" />
                <circle cx="72" cy="120" fill="#155E63" r="3.5" />
                <text fill="#181d1a" fontSize="11px" fontWeight="600" textAnchor="middle" x="72" y="124" fontFamily="Plus Jakarta Sans">
                  CORE NEXUS
                </text>
              </g>

              {/* Node 1: Dialectic (Terracotta) */}
              <g>
                <rect fill="#FFFFFF" height="40" rx="4" stroke="#D0C9BA" strokeWidth="1" width="100" x="230" y="35" />
                <rect fill="#C96B4B" height="3" rx="1.5" width="100" x="230" y="35" />
                <text fill="#181d1a" fontSize="11px" fontWeight="500" textAnchor="middle" x="280" y="59" fontFamily="Plus Jakarta Sans">
                  Dialectic
                </text>
              </g>

              {/* Node 2: Structure (Teal) */}
              <g>
                <rect fill="#FFFFFF" height="40" rx="4" stroke="#155E63" strokeWidth="1.5" width="105" x="230" y="100" />
                <rect fill="#155E63" height="3" rx="1.5" width="105" x="230" y="100" />
                <text fill="#155E63" fontSize="11px" fontWeight="600" textAnchor="middle" x="282.5" y="124" fontFamily="Plus Jakarta Sans">
                  Taxonomy
                </text>
              </g>

              {/* Node 3: Intuition (Olive) */}
              <g>
                <rect fill="#FFFFFF" height="40" rx="4" stroke="#D0C9BA" strokeWidth="1" width="100" x="230" y="165" />
                <rect fill="#899B72" height="3" rx="1.5" width="100" x="230" y="165" />
                <text fill="#181d1a" fontSize="11px" fontWeight="500" textAnchor="middle" x="280" y="189" fontFamily="Plus Jakarta Sans">
                  Intuition
                </text>
              </g>

              {/* Leaf Nodes */}
              <g>
                <rect fill="#FFFFFF" height="32" rx="4" stroke="#E2DCCE" width="85" x="440" y="24" />
                <circle cx="448" cy="40" fill="#899B72" r="3" />
                <text fill="#5B6362" fontSize="10px" textAnchor="middle" x="484" y="44" fontFamily="Plus Jakarta Sans">
                  Archival Seed
                </text>

                <rect fill="#FFFFFF" height="32" rx="4" stroke="#D0C9BA" width="85" x="440" y="74" />
                <rect fill="#155E63" height="2.5" rx="1" width="85" x="440" y="74" />
                <text fill="#181d1a" fontSize="10px" fontWeight="500" textAnchor="middle" x="482.5" y="94" fontFamily="Plus Jakarta Sans">
                  Ontology
                </text>

                <rect fill="#FFFFFF" height="32" rx="4" stroke="#D0C9BA" width="85" x="440" y="134" />
                <rect fill="#D6A84F" height="2.5" rx="1" width="85" x="440" y="134" />
                <text fill="#181d1a" fontSize="10px" fontWeight="500" textAnchor="middle" x="482.5" y="154" fontFamily="Plus Jakarta Sans">
                  Synthesis
                </text>

                <rect fill="#FFFFFF" height="32" rx="4" stroke="#E2DCCE" width="85" x="440" y="184" />
                <circle cx="448" cy="200" fill="#C96B4B" r="3" />
                <text fill="#5B6362" fontSize="10px" textAnchor="middle" x="484" y="204" fontFamily="Plus Jakarta Sans">
                  Draft Codex
                </text>
              </g>
            </svg>
          </div>
        </div>

        {/* Philosophical Footnote */}
        <div className="philosophical-footnote">
          <span className="footnote-rule" />
          <p className="footnote-text">
            Mind mapping as an intellectual craft — structured clarity drawn from ambient intuition.
          </p>
        </div>
      </div>

      {/* =========================================================
          RIGHT COLUMN: Centered Authentication Module
          ========================================================= */}
      <div className="auth-form-column">
        {/* Top Spacer / Atelier Status */}
        <div className="system-status-wrapper">
          <span className="system-status">
            <span className="status-online-dot" />
            Vault Nodes Online
          </span>
        </div>

        {/* Main Authentication Box */}
        <div className="auth-box">
          {/* Header */}
          <div className="auth-header">
            <span className="auth-protocol-label">
              {mode === 'signin' ? 'ACCESS PROTOCOL' : 'ATELIER REGISTRATION'}
            </span>
            <h2 className="auth-title">
              {mode === 'signin' ? 'Welcome back' : 'Create an account'}
            </h2>
            <p className="auth-subtitle">
              {mode === 'signin'
                ? 'Continue your inquiries and visual maps.'
                : 'Begin crafting topological visual workspaces.'}
            </p>
          </div>

          {/* Success Alert Banner */}
          {successMessage && (
            <div className="auth-alert auth-alert-success">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                check_circle
              </span>
              <span>{successMessage}</span>
            </div>
          )}

          {/* General Error Banner */}
          {errors.general && (
            <div className="auth-alert auth-alert-error">
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
                error
              </span>
              <span>{errors.general}</span>
            </div>
          )}

          {/* Form */}
          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            {/* Full Name Field (Sign Up Only) */}
            {mode === 'signup' && (
              <div className="form-field">
                <label className="field-label" htmlFor="register-name">
                  Full Name
                </label>
                <div className="input-wrapper">
                  <span className="material-symbols-outlined input-icon">person</span>
                  <input
                    id="register-name"
                    name="name"
                    type="text"
                    className={`field-input ${errors.name ? 'has-error' : ''}`}
                    placeholder="Ada Lovelace"
                    value={formData.name}
                    onChange={handleChange}
                  />
                </div>
                {errors.name && <span className="field-error-msg">{errors.name}</span>}
              </div>
            )}

            {/* Email Address Field */}
            <div className="form-field">
              <label className="field-label" htmlFor="auth-email">
                Email address
              </label>
              <div className="input-wrapper">
                <span className="material-symbols-outlined input-icon">alternate_email</span>
                <input
                  id="auth-email"
                  name="email"
                  type="email"
                  className={`field-input ${errors.email ? 'has-error' : ''}`}
                  placeholder="name@institution.org"
                  value={formData.email}
                  onChange={handleChange}
                />
              </div>
              {errors.email && <span className="field-error-msg">{errors.email}</span>}
            </div>

            {/* Password Field */}
            <div className="form-field">
              <div className="field-label-row">
                <label className="field-label" htmlFor="auth-password">
                  Password
                </label>
                {mode === 'signin' && (
                  <a href="#reset" className="field-link" onClick={(e) => e.preventDefault()}>
                    Forgot password?
                  </a>
                )}
              </div>
              <div className="input-wrapper">
                <span className="material-symbols-outlined input-icon">key</span>
                <input
                  id="auth-password"
                  name="password"
                  type={showPassword ? 'text' : 'password'}
                  className={`field-input ${errors.password ? 'has-error' : ''}`}
                  placeholder={mode === 'signin' ? 'Enter passphrase' : 'Create secure password'}
                  value={formData.password}
                  onChange={handleChange}
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
              {errors.password && <span className="field-error-msg">{errors.password}</span>}
            </div>

            {/* Confirm Password Field (Sign Up Only) */}
            {mode === 'signup' && (
              <div className="form-field">
                <label className="field-label" htmlFor="register-confirm-password">
                  Confirm Password
                </label>
                <div className="input-wrapper">
                  <span className="material-symbols-outlined input-icon">lock_reset</span>
                  <input
                    id="register-confirm-password"
                    name="confirmPassword"
                    type={showConfirmPassword ? 'text' : 'password'}
                    className={`field-input ${errors.confirmPassword ? 'has-error' : ''}`}
                    placeholder="Repeat password"
                    value={formData.confirmPassword}
                    onChange={handleChange}
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
                {errors.confirmPassword && (
                  <span className="field-error-msg">{errors.confirmPassword}</span>
                )}
              </div>
            )}

            {/* Remember Me Checkbox (Sign In Only) */}
            {mode === 'signin' && (
              <div className="form-options-row">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    name="rememberMe"
                    className="custom-checkbox"
                    checked={formData.rememberMe}
                    onChange={handleChange}
                  />
                  <span>Keep session active for 30 days</span>
                </label>
              </div>
            )}

            {/* Primary Submit Button */}
            <button type="submit" className="btn-auth-submit" disabled={loading}>
              <span>
                {loading
                  ? 'Connecting...'
                  : mode === 'signin'
                  ? 'Log in to workspace'
                  : 'Create atelier account'}
              </span>
              <span className="material-symbols-outlined btn-arrow-icon">arrow_forward</span>
            </button>
          </form>

          {/* Divider */}
          <div className="auth-divider">
            <div className="divider-line" />
            <span className="divider-text">or continue with</span>
          </div>

          {/* Google Federated Auth Button */}
          <button
            type="button"
            className="btn-google-auth"
            onClick={handleGoogleSignIn}
            disabled={loading}
          >
            <svg className="google-icon" viewBox="0 0 24 24">
              <path
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                fill="#4285F4"
              />
              <path
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                fill="#34A853"
              />
              <path
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
                fill="#FBBC05"
              />
              <path
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
                fill="#EA4335"
              />
            </svg>
            <span>Continue with Google</span>
          </button>

          {/* Mode Switch Prompt */}
          <div className="auth-switch-prompt">
            {mode === 'signin' ? (
              <>
                Don't have an account?
                <button
                  type="button"
                  className="switch-link-btn"
                  onClick={() => handleSwitchMode('signup')}
                >
                  Sign up
                </button>
              </>
            ) : (
              <>
                Already have an account?
                <button
                  type="button"
                  className="switch-link-btn"
                  onClick={() => handleSwitchMode('signin')}
                >
                  Sign in
                </button>
              </>
            )}
          </div>
        </div>

        {/* Atelier Protocol Footer Badge */}
        <div className="atelier-protocol-footer">
          <span>SECURE ATELIER PROTOCOL</span>
          <span>EST. 2024</span>
        </div>
      </div>
    </div>
  );
}
