// @vitest-environment jsdom

/**
 * Unit & Integration Tests for Issue S3-FE4:
 * Upload UI for New Types (DOCX, PPTX, and Seed URL)
 *
 * Requirements verified:
 * 1. File input accepts DOCX (.docx), PPTX (.pptx), and all previously supported types (.pdf, .txt, .png, etc.).
 * 2. "Paste a URL" input field rendered directly below the file attachment dropzone.
 * 3. Per-file validation and error messages:
 *    - Unsupported file types flagged with clear message.
 *    - Office files exceeding 20 MB flagged.
 *    - Standard files exceeding 25 MB flagged.
 *    - Invalid files are NOT silently discarded; each invalid file shows its own distinct error message.
 *    - Removing an invalid file clears the block.
 * 4. Per-URL validation and error messages:
 *    - Invalid URL format flagged next to the URL input.
 *    - Unreachable / private / localhost URLs flagged (avoiding unreliable browser-only CORS assumptions).
 *    - URL longer than 2000 characters flagged.
 * 5. Backend contract:
 *    - URL seed sent using the exact field name `seed_url` in FormData.
 *    - Mind map creation succeeds with URL seed alone (even without topic/thoughts/files).
 * 6. Mind map generation and existing file types continue to work.
 */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { mindMapService } from '../services/mindMapService'
import { apiClient } from '../services/apiClient'
import CreateMapPage from '../pages/CreateMapPage'
import { GenerationProgressView } from '../components/generator/GenerationProgressView'
import {
  validateFile,
  validateSeedUrl,
  SUPPORTED_EXTENSIONS,
  OFFICE_EXTENSIONS,
  MAX_OFFICE_FILE_BYTES,
  MAX_STANDARD_FILE_BYTES,
  formatFileSize,
} from '../utils/uploadValidation'

// Mock external navigation & context
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

let mockDraftMap = null
const mockSetDraftMap = vi.fn()

vi.mock('../context/MindMapContext', () => ({
  useMindMap: () => ({
    draftMap: mockDraftMap,
    setDraftMap: mockSetDraftMap,
  }),
  MindMapProvider: ({ children }) => children,
}))

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@atelier.craft', name: 'Test User' },
    logout: vi.fn(),
  }),
  AuthProvider: ({ children }) => children,
}))

function renderCreateMapPage(initialDraft = null) {
  mockDraftMap = initialDraft
  return render(
    <MemoryRouter>
      <CreateMapPage />
    </MemoryRouter>
  )
}

describe('S3-FE4: Upload UI for New Types (DOCX, PPTX, Seed URL)', () => {
  let capturedCalls = []
  const realFetch = globalThis.fetch

  const jsonResponse = (body, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => body,
    text: async () => JSON.stringify(body),
  })

  beforeEach(() => {
    capturedCalls = []
    mockDraftMap = null
    mockNavigate.mockClear()
    mockSetDraftMap.mockClear()
    globalThis.fetch = vi.fn(async (input, options) => {
      capturedCalls.push({ input, options })
      return jsonResponse({ id: 'new-map-uuid-123', title: 'Test Title' })
    })
  })

  afterEach(() => {
    cleanup()
    globalThis.fetch = realFetch
    vi.restoreAllMocks()
  })

  // =========================================================================
  // 1. Validation Utility Unit Tests
  // =========================================================================
  describe('uploadValidation Utility Functions', () => {
    it('recognizes DOCX and PPTX as supported extensions', () => {
      expect(SUPPORTED_EXTENSIONS).toContain('.docx')
      expect(SUPPORTED_EXTENSIONS).toContain('.pptx')
      expect(OFFICE_EXTENSIONS).toEqual(['.docx', '.pptx'])
    })

    it('preserves all standard file extensions (.pdf, .png, .jpg, .jpeg, .webp, .txt)', () => {
      expect(SUPPORTED_EXTENSIONS).toContain('.pdf')
      expect(SUPPORTED_EXTENSIONS).toContain('.png')
      expect(SUPPORTED_EXTENSIONS).toContain('.jpg')
      expect(SUPPORTED_EXTENSIONS).toContain('.jpeg')
      expect(SUPPORTED_EXTENSIONS).toContain('.webp')
      expect(SUPPORTED_EXTENSIONS).toContain('.txt')
    })

    it('validates valid DOCX and PPTX within 20 MB limit', () => {
      const docx = new File(['mock content'], 'paper.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })
      const pptx = new File(['mock content'], 'deck.pptx', {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      })

      expect(validateFile(docx).isValid).toBe(true)
      expect(validateFile(docx).error).toBeNull()

      expect(validateFile(pptx).isValid).toBe(true)
      expect(validateFile(pptx).error).toBeNull()
    })

    it('rejects unsupported file formats with informative message', () => {
      const exe = new File(['binary'], 'virus.exe', { type: 'application/octet-stream' })
      const res = validateFile(exe)
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/unsupported file type "\.exe"/i)
      expect(res.error).toMatch(/supported formats: pdf, png, jpg, webp, txt, docx, pptx/i)
    })

    it('rejects oversized DOCX file (> 20 MB)', () => {
      const oversizedDocx = {
        name: 'huge_thesis.docx',
        size: MAX_OFFICE_FILE_BYTES + 1024,
      }
      const res = validateFile(oversizedDocx)
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/larger than 20 MB/i)
    })

    it('rejects oversized standard file (> 25 MB)', () => {
      const oversizedPdf = {
        name: 'huge_scan.pdf',
        size: MAX_STANDARD_FILE_BYTES + 1024,
      }
      const res = validateFile(oversizedPdf)
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/larger than 25 MB/i)
    })

    it('rejects empty (0 byte) file', () => {
      const emptyFile = { name: 'empty.docx', size: 0 }
      const res = validateFile(emptyFile)
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/empty/i)
    })

    it('validates correct public HTTP and HTTPS seed URLs', () => {
      expect(validateSeedUrl('https://en.wikipedia.org/wiki/Knowledge_graph').isValid).toBe(true)
      expect(validateSeedUrl('http://example.com/research-paper.html').isValid).toBe(true)
      expect(validateSeedUrl('').isValid).toBe(true) // optional
    })

    it('rejects invalid URL format', () => {
      const res = validateSeedUrl('not-a-valid-url')
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/invalid url format/i)
    })

    it('rejects non-http schemes (ftp, javascript, file)', () => {
      const res1 = validateSeedUrl('ftp://files.example.com/doc')
      expect(res1.isValid).toBe(false)
      expect(res1.error).toMatch(/http:\/\/ or https:\/\//i)

      const res2 = validateSeedUrl('javascript:alert(1)')
      expect(res2.isValid).toBe(false)
      expect(res2.error).toMatch(/http:\/\/ or https:\/\//i)
    })

    it('rejects localhost, loopback, and private IP ranges (SSRF protection / reachability)', () => {
      expect(validateSeedUrl('http://localhost:8000/secret').isValid).toBe(false)
      expect(validateSeedUrl('http://127.0.0.1:3000').isValid).toBe(false)
      expect(validateSeedUrl('http://10.0.0.1/admin').isValid).toBe(false)
      expect(validateSeedUrl('http://192.168.1.50/api').isValid).toBe(false)
      expect(validateSeedUrl('http://172.16.0.1/dash').isValid).toBe(false)
      expect(validateSeedUrl('http://169.254.169.254/metadata').isValid).toBe(false)
      expect(validateSeedUrl('http://[::1]/internal').isValid).toBe(false)

      // IPv6 link-local (fe80::/10) and unique-local (fc00::/7)
      expect(validateSeedUrl('http://[fe80::1]/status').isValid).toBe(false)
      expect(validateSeedUrl('http://[fc00::1]/db').isValid).toBe(false)
      expect(validateSeedUrl('http://[fd12:3456:789a::1]/api').isValid).toBe(false)

      // Cloud metadata hostnames
      expect(validateSeedUrl('http://instance-data/latest').isValid).toBe(false)
      expect(validateSeedUrl('http://metadata/v1').isValid).toBe(false)
      expect(validateSeedUrl('http://metadata.google.internal/computeMetadata').isValid).toBe(false)

      const res = validateSeedUrl('http://localhost:5000')
      expect(res.error).toMatch(/public address, not localhost or a private network/i)
    })

    it('rejects URLs exceeding 2000 characters', () => {
      const longUrl = 'https://example.com/' + 'a'.repeat(2005)
      const res = validateSeedUrl(longUrl)
      expect(res.isValid).toBe(false)
      expect(res.error).toMatch(/too long/i)
    })
  })

  // =========================================================================
  // 2. Component Rendering & Form Attributes
  // =========================================================================
  describe('CreateMapPage Component Rendering (RTL)', () => {
    it('file input accepts DOCX, PPTX, and all standard formats', () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      expect(fileInput).toBeDefined()
      const accept = fileInput.getAttribute('accept')
      expect(accept).toContain('.docx')
      expect(accept).toContain('.pptx')
      expect(accept).toContain('.pdf')
      expect(accept).toContain('.png')
      expect(accept).toContain('.jpg')
      expect(accept).toContain('.jpeg')
      expect(accept).toContain('.webp')
      expect(accept).toContain('.txt')
    })

    it('renders "Paste a URL" input directly below the file attachment area', () => {
      renderCreateMapPage()
      const urlInput = screen.getByLabelText(/paste a url/i)
      expect(urlInput).toBeDefined()
      expect(urlInput.getAttribute('type')).toBe('url')
      expect(urlInput.getAttribute('placeholder')).toMatch(/https:\/\//i)

      // Verify DOM ordering: dropzone precedes URL field
      const dropzone = document.querySelector('.file-attachment-zone')
      const urlWrapper = document.querySelector('.url-input-container')
      expect(dropzone).toBeDefined()
      expect(urlWrapper).toBeDefined()
      expect(dropzone.compareDocumentPosition(urlWrapper) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })
  })

  // =========================================================================
  // 3. Per-File Validation & Error Messages (Do NOT Silently Discard)
  // =========================================================================
  describe('Per-File Validation & Individual Error Messaging', () => {
    it('accepts valid DOCX file and displays it in attached list without errors', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      const validDocx = new File(['valid word content'], 'research_paper.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })

      fireEvent.change(fileInput, { target: { files: [validDocx] } })

      expect(screen.getByText(/research_paper\.docx/i)).toBeDefined()
      expect(screen.queryByTestId('file-error-item')).toBeNull()
    })

    it('accepts valid PPTX file and displays it in attached list without errors', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      const validPptx = new File(['slides content'], 'lecture_slides.pptx', {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      })

      fireEvent.change(fileInput, { target: { files: [validPptx] } })

      expect(screen.getByText(/lecture_slides\.pptx/i)).toBeDefined()
      expect(screen.queryByTestId('file-error-item')).toBeNull()
    })

    it('does NOT silently discard an unsupported file type; shows per-file error message', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      const invalidFile = new File(['some data'], 'dataset.zip', { type: 'application/zip' })

      fireEvent.change(fileInput, { target: { files: [invalidFile] } })

      // File name is preserved in the UI
      expect(screen.getByText(/dataset\.zip/i)).toBeDefined()

      // Specific error message is displayed for that file
      const errorItem = screen.getByTestId('file-error-item')
      expect(errorItem).toBeDefined()
      expect(errorItem.textContent).toMatch(/unsupported file type/i)
      expect(errorItem.textContent).toMatch(/docx, pptx/i)
    })

    it('does NOT silently discard an oversized Office document (> 20 MB); shows distinct 20 MB error', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')

      // Create dummy file representation with size > 20 MB
      const oversizedFile = new File(['a'], 'huge_deck.pptx', {
        type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      })
      Object.defineProperty(oversizedFile, 'size', { value: 21 * 1024 * 1024 })

      fireEvent.change(fileInput, { target: { files: [oversizedFile] } })

      expect(screen.getByText(/huge_deck\.pptx/i)).toBeDefined()
      const errorMsg = screen.getByTestId('file-error-message')
      expect(errorMsg.textContent).toMatch(/larger than 20 MB/i)
    })

    it('does NOT silently discard an oversized standard file (> 25 MB); shows distinct 25 MB error', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')

      const oversizedPdf = new File(['a'], 'giant_scan.pdf', { type: 'application/pdf' })
      Object.defineProperty(oversizedPdf, 'size', { value: 26 * 1024 * 1024 })

      fireEvent.change(fileInput, { target: { files: [oversizedPdf] } })

      expect(screen.getByText(/giant_scan\.pdf/i)).toBeDefined()
      const errorMsg = screen.getByTestId('file-error-message')
      expect(errorMsg.textContent).toMatch(/larger than 25 MB/i)
    })

    it('handles multiple files simultaneously with individual error messages for each invalid file', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')

      const goodDocx = new File(['good content'], 'notes.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })
      const badExe = new File(['binary'], 'installer.exe', { type: 'application/octet-stream' })
      const badZip = new File(['archive'], 'backup.tar.gz', { type: 'application/gzip' })

      fireEvent.change(fileInput, { target: { files: [goodDocx, badExe, badZip] } })

      // All files are visible in DOM (no silent discard)
      expect(screen.getByText(/notes\.docx/i)).toBeDefined()
      expect(screen.getByText(/installer\.exe/i)).toBeDefined()
      expect(screen.getByText(/backup\.tar\.gz/i)).toBeDefined()

      // Exactly two error pills
      const errorItems = screen.getAllByTestId('file-error-item')
      expect(errorItems).toHaveLength(2)

      // Each has its own distinct message
      expect(errorItems[0].textContent).toMatch(/unsupported file type/i)
      expect(errorItems[1].textContent).toMatch(/unsupported file type/i)
    })

    it('allows removing an invalid file individually', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      const badFile = new File(['bad'], 'script.sh', { type: 'text/x-sh' })

      fireEvent.change(fileInput, { target: { files: [badFile] } })
      expect(screen.getByText(/script\.sh/i)).toBeDefined()

      const removeBtn = screen.getByRole('button', { name: /remove script\.sh/i })
      fireEvent.click(removeBtn)

      expect(screen.queryByText(/script\.sh/i)).toBeNull()
      expect(screen.queryByTestId('file-error-item')).toBeNull()
    })

    it('blocks submission while an invalid file is present', async () => {
      renderCreateMapPage()
      const fileInput = document.querySelector('input[type="file"]')
      const badFile = new File(['bad'], 'bad.exe', { type: 'application/octet-stream' })

      fireEvent.change(fileInput, { target: { files: [badFile] } })

      // Also provide a topic so it is not rejected for empty topic
      const topicInput = screen.getByLabelText(/what's the main idea/i)
      fireEvent.change(topicInput, { target: { value: 'Quantum Mechanics' } })

      const submitBtn = screen.getByRole('button', { name: /generate mind map/i })
      fireEvent.click(submitBtn)

      // Submit blocked with error
      expect(screen.getByText(/please remove invalid files before generating/i)).toBeDefined()
      expect(mockNavigate).not.toHaveBeenCalled()
    })
  })

  // =========================================================================
  // 4. Per-URL Validation & Error Messaging
  // =========================================================================
  describe('Per-URL Validation & Inline Error Messaging', () => {
    it('shows error next to URL input when invalid URL format is entered', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'not-a-valid-http-url')
      fireEvent.blur(urlInput)

      const urlError = screen.getByTestId('url-error-message')
      expect(urlError).toBeDefined()
      expect(urlError.textContent).toMatch(/url must start with http:\/\/ or https:\/\//i)
    })

    it('shows reachability error next to URL input when localhost or private IP is entered', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'http://localhost:3000/internal-page')
      fireEvent.blur(urlInput)

      const urlError = screen.getByTestId('url-error-message')
      expect(urlError).toBeDefined()
      expect(urlError.textContent).toMatch(/public address, not localhost or a private network/i)
    })

    it('clears URL error when valid URL is typed', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'bad-url')
      fireEvent.blur(urlInput)
      expect(screen.getByTestId('url-error-message')).toBeDefined()

      await user.clear(urlInput)
      await user.type(urlInput, 'https://arxiv.org/abs/2301.00001')
      fireEvent.blur(urlInput)

      expect(screen.queryByTestId('url-error-message')).toBeNull()
    })

    it('clears URL and error when clear button is clicked', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'https://example.com')

      const clearBtn = screen.getByRole('button', { name: /clear url/i })
      await user.click(clearBtn)

      expect(urlInput.value).toBe('')
      expect(screen.queryByTestId('url-error-message')).toBeNull()
    })

    it('blocks submission if URL has validation error', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'http://192.168.1.1/secret')

      const topicInput = screen.getByLabelText(/what's the main idea/i)
      await user.type(topicInput, 'Network Security')

      const submitBtn = screen.getByRole('button', { name: /generate mind map/i })
      await user.click(submitBtn)

      expect(screen.getByTestId('url-error-message')).toBeDefined()
      expect(mockNavigate).not.toHaveBeenCalled()
    })
  })

  // =========================================================================
  // 5. Backend Contract & Payload Delivery
  // =========================================================================
  describe('Backend Generation Contract (seed_url & FormData)', () => {
    it('mindMapService.generateMap includes seed_url in FormData payload', async () => {
      await mindMapService.generateMap('map-789', {
        topic: 'Graph Theory',
        seed_url: 'https://en.wikipedia.org/wiki/Graph_theory',
      })

      expect(capturedCalls).toHaveLength(1)
      const call = capturedCalls[0]
      expect(call.options.body).toBeInstanceOf(FormData)
      expect(call.options.body.get('seed_url')).toBe('https://en.wikipedia.org/wiki/Graph_theory')
      expect(call.options.body.get('topic')).toBe('Graph Theory')
    })

    it('mindMapService.generateMap accepts seedUrl alias and formats as seed_url', async () => {
      await mindMapService.generateMap('map-789', {
        topic: 'Graph Theory',
        seedUrl: 'https://en.wikipedia.org/wiki/Eulerian_path',
      })

      expect(capturedCalls).toHaveLength(1)
      const call = capturedCalls[0]
      expect(call.options.body.get('seed_url')).toBe('https://en.wikipedia.org/wiki/Eulerian_path')
    })

    it('submitting with seed_url alone succeeds and navigates with draft state', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'https://en.wikipedia.org/wiki/Neural_network')

      const submitBtn = screen.getByRole('button', { name: /generate mind map/i })
      await user.click(submitBtn)

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledTimes(1)
      })

      const [navPath, navOptions] = mockNavigate.mock.calls[0]
      expect(navPath).toBe('/generating')
      expect(navOptions.state.seed_url).toBe('https://en.wikipedia.org/wiki/Neural_network')
      expect(mockSetDraftMap).toHaveBeenCalledWith(
        expect.objectContaining({
          seed_url: 'https://en.wikipedia.org/wiki/Neural_network',
        })
      )
    })

    it('submitting with DOCX file and seed_url delivers both in draft state', async () => {
      const user = userEvent.setup()
      renderCreateMapPage()

      // Attach valid DOCX
      const fileInput = document.querySelector('input[type="file"]')
      const validDocx = new File(['word content'], 'thesis.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      })
      fireEvent.change(fileInput, { target: { files: [validDocx] } })

      // Paste seed URL
      const urlInput = screen.getByLabelText(/paste a url/i)
      await user.type(urlInput, 'https://arxiv.org/abs/2301.00002')

      const submitBtn = screen.getByRole('button', { name: /generate mind map/i })
      await user.click(submitBtn)

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledTimes(1)
      })

      const navState = mockNavigate.mock.calls[0][1].state
      expect(navState.seed_url).toBe('https://arxiv.org/abs/2301.00002')
      expect(navState.files).toHaveLength(1)
      expect(navState.files[0].name).toBe('thesis.docx')
    })
  })

  // =========================================================================
  // 6. Complete Generation Error Flow (Unreachable URL / Worker Rejection)
  // =========================================================================
  describe('Generation Error Flow (Unreachable URL / Worker Rejection)', () => {
    it('displays clear error and "Back to Create" button when backend rejects URL seed at dispatch', async () => {
      vi.spyOn(mindMapService, 'generateMap').mockRejectedValueOnce(
        new Error('URL unreachable: Could not establish connection to host')
      )
      const deleteSpy = vi.spyOn(mindMapService, 'deleteMap').mockResolvedValue({})

      render(
        <MemoryRouter>
          <GenerationProgressView
            mapId="map-unreachable-1"
            initialParams={{
              topic: 'Unreachable Site',
              seed_url: 'https://nonexistent-domain-12345.org',
            }}
          />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(
          screen.getByText(/URL unreachable: Could not establish connection to host/i)
        ).toBeDefined()
      })

      // Cleaned up empty draft map
      expect(deleteSpy).toHaveBeenCalledWith('map-unreachable-1')

      // Displays Back to Create button
      const backBtn = screen.getByRole('button', { name: /back to create/i })
      expect(backBtn).toBeDefined()
      fireEvent.click(backBtn)
      expect(mockNavigate).toHaveBeenCalledWith('/create')
    })

    it('displays clear worker rejection message when job status transitions to rejected', async () => {
      vi.spyOn(mindMapService, 'pollJobStatus').mockResolvedValueOnce({
        id: 'job-rejected-1',
        status: 'rejected',
        error_message: 'Worker rejected seed URL: Target server timed out or returned HTTP 504',
      })
      const deleteSpy = vi.spyOn(mindMapService, 'deleteMap').mockResolvedValue({})

      render(
        <MemoryRouter>
          <GenerationProgressView
            mapId="map-rejected-1"
            jobId="job-rejected-1"
            initialParams={null}
          />
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(
          screen.getByText(/Worker rejected seed URL: Target server timed out or returned HTTP 504/i)
        ).toBeDefined()
      })

      expect(deleteSpy).toHaveBeenCalledWith('map-rejected-1')
      expect(screen.getByRole('button', { name: /back to create/i })).toBeDefined()
    })
  })
})
