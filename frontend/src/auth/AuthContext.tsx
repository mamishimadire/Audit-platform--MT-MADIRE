import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { apiClient, getStoredToken, registerUnauthorizedHandler, setStoredToken } from '../lib/apiClient'
import type { UserOut } from '../types/api'

interface AuthContextValue {
  user: UserOut | null
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (...roleNames: string[]) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null)
  const [isLoading, setIsLoading] = useState(true)

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
    <AuthContext.Provider value={{ user, isLoading, login, logout, hasRole }}>{children}</AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
