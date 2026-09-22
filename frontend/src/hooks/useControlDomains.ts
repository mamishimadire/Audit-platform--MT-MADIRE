import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import type { ControlLibraryOut } from '../types/api'

/**
 * control_code -> domain (audit category, e.g. "User Access Management"),
 * fetched once from the global control library (authenticated-only, not
 * organization-scoped — see backend routes/control_library.py). Exceptions
 * and Findings only carry control_code/control_name, not domain, so this
 * is how those pages group and search by the same category the Controls
 * page groups by, with no change to either backend response.
 */
export function useControlDomains(): Map<string, string> {
  const [domains, setDomains] = useState<Map<string, string>>(new Map())

  useEffect(() => {
    apiClient.get<ControlLibraryOut[]>('/control-library').then((res) => {
      setDomains(new Map(res.data.map((entry) => [entry.control_code, entry.domain])))
    })
  }, [])

  return domains
}
