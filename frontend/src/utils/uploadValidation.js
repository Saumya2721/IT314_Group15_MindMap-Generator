/**
 * Upload & URL Seed Validation Utilities for MindWeave (S3-FE4)
 *
 * Implements client-side validation aligned with backend intake constraints (S3-BE7):
 * - File formats: PDF, PNG, JPEG, WEBP, TXT, DOCX, PPTX
 * - Size limits: 20 MB for Office docs (DOCX/PPTX), 25 MB for standard files
 * - Seed URL: http(s) scheme, max 2000 chars, public hostname, SSRF-guarded (no localhost/private IP)
 */

export const SUPPORTED_EXTENSIONS = [
  '.pdf',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.txt',
  '.docx',
  '.pptx',
]

export const OFFICE_EXTENSIONS = ['.docx', '.pptx']

export const MAX_STANDARD_FILE_BYTES = 25 * 1024 * 1024 // 25 MB
export const MAX_OFFICE_FILE_BYTES = 20 * 1024 * 1024   // 20 MB
export const MAX_SEED_URL_LENGTH = 2000                 // 2000 chars

/**
 * Format raw bytes into human-readable size string.
 */
export function formatFileSize(bytes) {
  if (typeof bytes !== 'number' || isNaN(bytes) || bytes < 0) return '0 KB'
  if (bytes >= 1024 * 1024) {
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }
  return (bytes / 1024).toFixed(1) + ' KB'
}

/**
 * Extract lowercase file extension from filename.
 */
export function getFileExtension(filename = '') {
  const cleanName = (filename || '').trim()
  const lastDot = cleanName.lastIndexOf('.')
  if (lastDot === -1 || lastDot === cleanName.length - 1) return ''
  return cleanName.slice(lastDot).toLowerCase()
}

/**
 * Validate an uploaded File object against type and size limits.
 *
 * @param {File} file - Browser File object
 * @returns {{ isValid: boolean, error: string | null }}
 */
export function validateFile(file) {
  if (!file || typeof file !== 'object') {
    return { isValid: false, error: 'Invalid file object.' }
  }

  const name = file.name || 'unnamed_file'
  const ext = getFileExtension(name)

  // 1. Check supported extension
  if (!ext || !SUPPORTED_EXTENSIONS.includes(ext)) {
    return {
      isValid: false,
      error: `Unsupported file type "${ext || 'unknown'}". Supported formats: PDF, PNG, JPG, WEBP, TXT, DOCX, PPTX.`,
    }
  }

  // 2. Check empty file
  if (file.size === 0) {
    return {
      isValid: false,
      error: 'The file is empty (0 bytes).',
    }
  }

  // 3. Check size limits
  const isOffice = OFFICE_EXTENSIONS.includes(ext)
  const maxBytes = isOffice ? MAX_OFFICE_FILE_BYTES : MAX_STANDARD_FILE_BYTES
  const maxMb = isOffice ? 20 : 25

  if (file.size > maxBytes) {
    return {
      isValid: false,
      error: `File is larger than ${maxMb} MB (${formatFileSize(file.size)}).`,
    }
  }

  return { isValid: true, error: null }
}

/**
 * Check if a hostname represents a private, loopback, or internal address (SSRF guard).
 *
 * Aligned with backend SSRF protection:
 * - RFC 1918 IPv4 private networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
 * - Loopback addresses (127.0.0.0/8, ::1/128)
 * - Link-local addresses (169.254.0.0/16, fe80::/10)
 * - Unique local IPv6 addresses (fc00::/7)
 * - Cloud metadata endpoints (169.254.169.254, instance-data, metadata.google.internal)
 *
 * @param {string} hostname
 * @returns {boolean} true if private/unreachable from public web
 */
function isPrivateOrLocalHost(hostname) {
  const h = (hostname || '').toLowerCase().trim()
  if (!h) return true

  // Strip brackets if IPv6 literal e.g. [::1] -> ::1
  const rawHost = h.replace(/^\[|\]$/g, '')

  // Localhost / common local & cloud metadata hostnames
  if (
    h === 'localhost' ||
    h === '0.0.0.0' ||
    h === '127.0.0.1' ||
    rawHost === '::1' ||
    rawHost === '::' ||
    rawHost === '0:0:0:0:0:0:0:1' ||
    rawHost === 'instance-data' ||
    rawHost === 'metadata' ||
    h.endsWith('.localhost') ||
    h.endsWith('.internal') ||
    h.endsWith('.local')
  ) {
    return true
  }

  // Check IPv4 ranges
  const parts = rawHost.split('.')
  if (parts.length === 4 && parts.every((p) => /^\d+$/.test(p) && Number(p) >= 0 && Number(p) <= 255)) {
    const [b1, b2] = parts.map(Number)
    if (
      b1 === 0 ||                        // 0.0.0.0/8
      b1 === 10 ||                       // 10.0.0.0/8
      b1 === 127 ||                      // 127.0.0.0/8 (loopback)
      (b1 === 169 && b2 === 254) ||      // 169.254.0.0/16 (link-local & cloud metadata)
      (b1 === 172 && b2 >= 16 && b2 <= 31) || // 172.16.0.0/12 (private B)
      (b1 === 192 && b2 === 168)        // 192.168.0.0/16 (private C)
    ) {
      return true
    }
  }

  // Check IPv6 link-local (fe80::/10) and unique-local (fc00::/7)
  if (
    rawHost.startsWith('fe8') ||
    rawHost.startsWith('fe9') ||
    rawHost.startsWith('fea') ||
    rawHost.startsWith('feb') ||
    rawHost.startsWith('fc') ||
    rawHost.startsWith('fd')
  ) {
    return true
  }

  return false
}

/**
 * Validate a seed URL string for correct format and reachability constraints.
 *
 * Avoids unreliable browser-only CORS fetch assumptions while rigorously enforcing:
 * - Scheme (http/https only)
 * - Maximum length (2000 characters)
 * - Valid public hostname (rejects localhost, private IPs, loopbacks)
 *
 * @param {string} url - Candidate URL
 * @returns {{ isValid: boolean, error: string | null }}
 */
export function validateSeedUrl(url) {
  if (!url || !url.trim()) {
    // Optional field: empty string is valid
    return { isValid: true, error: null }
  }

  const trimmed = url.trim()

  // 1. Length cap
  if (trimmed.length > MAX_SEED_URL_LENGTH) {
    return {
      isValid: false,
      error: `URL is too long (max ${MAX_SEED_URL_LENGTH} characters).`,
    }
  }

  // 2. Parse URL structure
  let parsed
  try {
    parsed = new URL(trimmed)
  } catch {
    return {
      isValid: false,
      error: 'Invalid URL format. URL must start with http:// or https://.',
    }
  }

  // 3. Scheme check
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return {
      isValid: false,
      error: 'URL must start with http:// or https://.',
    }
  }

  // 4. Hostname presence
  const hostname = (parsed.hostname || '').toLowerCase().trim()
  if (!hostname) {
    return {
      isValid: false,
      error: 'URL must have a valid hostname.',
    }
  }

  // 5. SSRF / Unreachable private or local host check
  if (isPrivateOrLocalHost(hostname)) {
    return {
      isValid: false,
      error: 'URL must point to a public address, not localhost or a private network.',
    }
  }

  // 6. Domain structure check (must have top-level domain if not a public IP)
  const isIpv4 = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname)
  const isIpv6 = hostname.startsWith('[') && hostname.endsWith(']')
  if (!isIpv4 && !isIpv6 && (!hostname.includes('.') || hostname.endsWith('.'))) {
    return {
      isValid: false,
      error: 'Invalid URL format. Hostname must be a valid public domain.',
    }
  }

  return { isValid: true, error: null }
}
