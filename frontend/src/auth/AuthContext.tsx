import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { apiClient, getStoredToken, registerUnauthorizedHandler, setStoredToken } from '../lib/apiClient'
import { setActiveSoundUser } from '../lib/clickSound'
import type { UserOut } from '../types/api'

interface AuthContextValue {
  user: UserOut | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (...roleNames: string[]) => boolean
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<UserOut | null>(null)
  const [isLoading, setIsLoading] = useState(true)

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
    registerUnauthorizedHandler(() => {
      setStoredToken(null)
      setUser(null)
    })

    const token = getStoredToken()
    if (!token) {
      setIsLoading(false)
      return
    }
    fetchMe().finally(() => setIsLoading(false))
  }, [])

  // Sliding expiration: the access token has a fixed lifetime
  // (access_token_expire_minutes, 30 by default) with no refresh at all
  // otherwise — an actively-working user would get logged out mid-session
  // the instant it elapsed, no matter how recently they last clicked
  // anything. Re-issuing it well before that on a timer, for as long as
  // the app stays open with someone logged in, means only genuine
  // inactivity (closing the tab, the browser sleeping) ever lets it
  // really expire. /auth/refresh re-checks the account is still active,
  // so this can't extend a session past what a fresh login would allow.
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
    setStoredToken(response.data.access_token)
    await fetchMe()
  }

  const logout = () => {
    setStoredToken(null)
    setUser(null)
  }

  const hasRole = (...roleNames: string[]) => user !== null && roleNames.some((r) => user.roles.includes(r))

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, hasRole, refreshUser: fetchMe }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
