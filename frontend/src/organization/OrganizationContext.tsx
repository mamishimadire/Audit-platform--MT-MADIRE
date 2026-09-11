import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import type { OrganizationOut } from '../types/api'

const STORAGE_KEY = 'audit_platform_selected_org'

interface OrganizationContextValue {
  organizationId: string | null
  setOrganizationId: (id: string) => void
  organizations: OrganizationOut[]
  needsPicker: boolean
  /** True only while the organization list is being fetched for the first time — false once it resolves, even to zero results. */
  isLoading: boolean
  /** Set if the fetch itself failed (network/auth/server error) — distinct from "loaded, zero organizations." */
  loadError: boolean
}

const OrganizationContext = createContext<OrganizationContextValue | null>(null)

/**
 * A platform user (no organization_id of their own) picks which client
 * they're looking at. That choice used to live in each page's own local
 * state, so it silently reset to "first org in the list" on every
 * navigation — jarring once there's more than a couple of clients.
 * Lifted here and persisted to localStorage (a per-browser UI convenience,
 * not data) so it survives moving between pages.
 *
 * isLoading/loadError exist because "still fetching" and "fetched
 * successfully but there are zero organizations" were previously
 * indistinguishable to consumers — a brand-new platform admin with no
 * client organizations yet saw an infinite spinner instead of an honest
 * "you haven't added any clients yet" state.
 */
export function OrganizationProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [organizations, setOrganizations] = useState<OrganizationOut[]>([])
  const [organizationId, setOrganizationIdState] = useState<string | null>(user?.organization_id ?? null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    if (user?.organization_id) {
      setOrganizationIdState(user.organization_id)
      setIsLoading(false)
      return
    }
    setIsLoading(true)
    setLoadError(false)
    apiClient
      .get<OrganizationOut[]>('/organizations')
      .then((res) => {
        setOrganizations(res.data)
        setOrganizationIdState((current) => {
          if (current && res.data.some((o) => o.organization_id === current)) return current
          let stored: string | null = null
          try {
            stored = localStorage.getItem(STORAGE_KEY)
          } catch {
            // ignore — private browsing / storage disabled
          }
          if (stored && res.data.some((o) => o.organization_id === stored)) return stored
          return res.data[0]?.organization_id ?? null
        })
      })
      .catch(() => {
        setLoadError(true)
      })
      .finally(() => {
        setIsLoading(false)
      })
  }, [user])

  const setOrganizationId = (id: string) => {
    setOrganizationIdState(id)
    try {
      localStorage.setItem(STORAGE_KEY, id)
    } catch {
      // ignore
    }
  }

  return (
    <OrganizationContext.Provider
      value={{ organizationId, setOrganizationId, organizations, needsPicker: !user?.organization_id, isLoading, loadError }}
    >
      {children}
    </OrganizationContext.Provider>
  )
}

export function useActiveOrganization(): OrganizationContextValue {
  const ctx = useContext(OrganizationContext)
  if (!ctx) throw new Error('useActiveOrganization must be used within an OrganizationProvider')
  return ctx
}
