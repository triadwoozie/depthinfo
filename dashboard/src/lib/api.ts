/** Backend endpoints. Override with NEXT_PUBLIC_API_URL (e.g. when the dashboard is served elsewhere). */
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "")
export const WS_URL = `${API_BASE.replace(/^http/, "ws")}/ws/telemetry`

/** Resolve a backend-relative path (e.g. "/exports/scan.blend") to an absolute URL. */
export function apiUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_BASE}${path}`
}
