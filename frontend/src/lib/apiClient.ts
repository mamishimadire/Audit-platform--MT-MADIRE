import axios from 'axios'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
})

// Exported (not just used internally) so AuthContext can tell a `storage`
// event about OUR token apart from an unrelated key another feature wrote
// (e.g. clickSound's per-user sound preference) — see its cross-tab sync.
export const TOKEN_KEY = 'audit_platform_access_token'

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setStoredToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

apiClient.interceptors.request.use((config) => {
  const token = getStoredToken()
  if (token) {
    config.headers = config.headers ?? {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/** Set once by AuthProvider so a 401 anywhere can force a clean logout. The
 * detail string is passed through so a specific cause (e.g. "signed in
 * somewhere else" — see api.deps.get_current_user) can be shown, instead of
 * a plain, unexplained bounce back to the login page. */
let onUnauthorized: ((detail?: string) => void) | null = null
export function registerUnauthorizedHandler(handler: (detail?: string) => void): void {
  onUnauthorized = handler
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const detail = error.response?.data?.detail
      onUnauthorized?.(typeof detail === 'string' ? detail : undefined)
    }
    return Promise.reject(error)
  },
)
