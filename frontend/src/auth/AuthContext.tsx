import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { apiClient, getStoredToken, registerUnauthorizedHandler, setStoredToken, TOKEN_KEY } from '../lib/apiClient'
import { setActiveSoundUser } from '../lib/clickSound'
import type { UserOut } from '../types/api'

interface AuthContextValue {
  user: UserOut | null
  isLoading: boolean
  // Why the last sign-out happened, when it wasn't the user's own click —
  // e.g. "this account was signed in somewhere else" (see api.deps.
  // get_current_user's single-session check). Shown once on the login
  // page, then cleared.
  authMessage: string | null
  clearAuthMessage: () => void
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (...roleNames: string[]) => boolean
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<UserOut | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [authMessage, setAuthMessage] = useState<string | null>(null)
  // Suppresses authMessage for the specific window right after THIS tab's
  // own deliberate logout() — clearing the token can race an unrelated
  // in-flight poll (the notification bell, pending-approvals, ...), which
  // then 401s with no token at all and would otherwise show a confusing
  // "Not authenticated" as if the session had ended unexpectedly, right
  // after the user chose to end it themselves. Reset the moment someone
  // signs back in, so a LATER, genuine unexpected sign-out still shows.
  const loggingOut = useRef(false)

  // Every place the current user changes goes through this — never the
  // raw setUserState — so the per-user sound-preference scope (see
  // clickSound.setActiveSoundUser) can never drift out of sync with who's
  // actually logged in.
  const setUser = (next: UserOut | null) => {
    setUserState(next)
    setActiveSoundUser(next?.user_id ?? null)
  }

  const fetchMe = async () => {
    const response = await apiClient.get<UserOut>('/auth/me')
    setUser(response.data)
  }

  useEffect(() => {
    registerUnauthorizedHandler((detail) => {
      setStoredToken(null)
      setUser(null)
      if (detail && !loggingOut.current) setAuthMessage(detail)
    })

    const token = getStoredToken()
    if (!token) {
      setIsLoading(false)
      return
    }
    fetchMe().finally(() => setIsLoading(false))
  }, [])

  // The single browser this account is signed in with is only ONE browser
  // process, not one tab — a second tab shares the same localStorage, so
  // logging in as someone else there (or logging out, or getting signed
  // out elsewhere and the token being cleared) changes the SAME key this
  // tab is still holding onto in memory. Without this, a background tab
  // just keeps showing its old user's screen while silently making every
  // further API call as whoever the token now belongs to — a full reload
  // is the only way to guarantee this tab's state (not just auth, every
  // page's own data) can't keep reflecting a identity it no longer has.
  // `storage` only fires in OTHER tabs, never the one that made the
  // change, so the tab where someone actually logged in/out is unaffected.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== TOKEN_KEY) return
      if (event.newValue === event.oldValue) return
      window.location.reload()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  // Sliding expiration: the access token has a fixed lifetime
  // (access_token_expire_minutes, 30 by default) with no refresh at all
  // otherwise — an actively-working user would get logged out mid-session
  // the instant it elapsed, no matter how recently they last clicked
  // anything. Re-issuing it well before that on a timer, for as long as
  // the app stays open with someone logged in, means only genuine
  // inactivity (closing the tab, the browser sleeping) ever lets it
  // really expire. /auth/refresh re-checks the account is still active
  // AND that this is still its one current session (see get_current_user),
  // so this can't extend a session past what a fresh login would allow,
  // and stops immediately if the account signs in somewhere else.
  useEffect(() => {
    if (!user) return
    const REFRESH_INTERVAL_MS = 10 * 60 * 1000
    const interval = setInterval(() => {
      apiClient
        .post<{ access_token: string }>('/auth/refresh')
        .then((response) => setStoredToken(response.data.access_token))
        .catch(() => {
          /* a genuinely expired/invalid token here is handled by the
             401 interceptor already — nothing extra to do */
        })
    }, REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [user])

  const login = async (email: string, password: string) => {
    const form = new URLSearchParams()
    form.set('username', email)
    form.set('password', password)
    const response = await apiClient.post<{ access_token: string }>('/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    loggingOut.current = false
    setAuthMessage(null)
    setStoredToken(response.data.access_token)
    await fetchMe()
  }

  const logout = () => {
    loggingOut.current = true
    // Best-effort: tells the server to end THIS session too (see
    // /auth/logout), so a copy of the token stops working, not just this
    // browser's own memory of it. The local sign-out below never waits on
    // it — an unreachable server must never block someone from signing out.
    apiClient.post('/auth/logout').catch(() => {})
    setStoredToken(null)
    setUser(null)
    setAuthMessage(null)
  }

  const hasRole = (...roleNames: string[]) => user !== null && roleNames.some((r) => user.roles.includes(r))

  return (
    <AuthContext.Provider
      value={{ user, isLoading, authMessage, clearAuthMessage: () => setAuthMessage(null), login, logout, hasRole, refreshUser: fetchMe }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
