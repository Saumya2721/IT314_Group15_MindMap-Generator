# Export Conversion Service Specification (S2-BE6 / U7 / M4)

## 1. Overview

The Mind Map Generator supports exporting diagrams into multiple formats:
- **SVG / JSON**: Rendered directly by the FastAPI backend or client canvas.
- **PDF / PNG**: Rendered via a dedicated headless conversion adapter (`ExportConversionAdapter`) that communicates over HTTP with a headless rendering service.
- **Client-Side Fallback**: If the server-side export converter service is offline or unreachable, the browser client provides direct canvas-based SVG and PNG export via HTML5 `<canvas>` rendering.

---

## 2. Headless Conversion Service Contract

### Endpoint Specification
- **Method:** `POST`
- **Path:** `/convert` (configurable via `EXPORT_CONVERTER_URL`)
- **Headers:** `Content-Type: application/json`

### Request Payload Schema
```json
{
  "svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" ...>...</svg>",
  "format": "pdf",
  "scale": 2.0
}
```

| Field | Type | Description |
|---|---|---|
| `svg` | string | Full valid SVG document string to render. |
| `format` | string | Target output format: `"pdf"` or `"png"`. (If `"svg"`, the adapter returns the SVG bytes directly without making an HTTP request). |
| `scale` | float | Rasterization scale factor (default: `2.0` for high-resolution displays). |

### Response Schema
- **Success (HTTP 200):**
  - `Content-Type: application/pdf` or `image/png`
  - Binary response body containing converted file bytes.
- **Client Error (HTTP 4xx):**
  - Raised when input SVG is malformed or parameters are invalid.
- **Server Error (HTTP 5xx):**
  - Triggers backend exponential retry up to 2 attempts.

---

## 3. Configuration & Environment Variables

| Variable | Default Value | Description |
|---|---|---|
| `EXPORT_CONVERTER_URL` | `http://localhost:3001/convert` | Full HTTP URL to the conversion endpoint. |
| `EXPORT_TIMEOUT_SECONDS` | `15.0` | HTTP request timeout in seconds before aborting conversion. |

### Retry & Resilience Policy
- **Max Retries:** 2 retries (total 3 attempts).
- **Backoff:** Linear sleep $0.3 \times \text{attempt}$ seconds between failed attempts.
- **Failure Status Code:** When all attempts fail, the FastAPI route catches `ExportConversionError` and returns `HTTP 502 Bad Gateway` with clear guidance to utilize client-side SVG/PNG export.

---

## 4. Deployment Models

### Model A: External Managed / Microservice (Default)
In production or cloud environments, the converter runs as an isolated microservice (e.g., Gotenberg, Playwright/Puppeteer server, or Cloud Run instance) to isolate headless Chromium memory and compute consumption from the core API. Set `EXPORT_CONVERTER_URL` to point to the service URL.

### Model B: Docker Compose Microservice
To run the converter locally with Docker Compose, add a lightweight container to `compose.yaml`:
```yaml
  converter:
    image: gotenberg/gotenberg:8
    ports: ["3001:3000"]
    restart: unless-stopped
```
And set `EXPORT_CONVERTER_URL=http://converter:3000/convert` in `backend/.env`.

---

## 5. Client-Side Fallback Behavior

When `ExportConversionError` is returned by `/api/maps/{map_id}/export?format=pdf`:
1. The backend returns HTTP 502 with JSON error:
   ```json
   {
     "detail": "Export conversion service unavailable: Converter connection/timeout error. Client-side SVG export is available via format=svg."
   }
   ```
2. The frontend web UI intercepts this error and seamlessly falls back to client-side HTML5 Canvas rendering to generate and download PNG / SVG directly without user interruption.
