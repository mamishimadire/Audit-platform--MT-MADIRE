import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '../lib/apiClient'
import type { PendingApprovalOut } from '../types/api'

const POLL_INTERVAL_MS = 30_000

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(ms / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

export function NotificationBell({ organizationId }: { organizationId: string | null }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<PendingApprovalOut[]>([])
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!organizationId) {
      setItems([])
      return
    }
    let cancelled = false
    const load = () => {
      apiClient
        .get<PendingApprovalOut[]>(`/organizations/${organizationId}/pending-approvals`)
        .then((res) => {
          if (!cancelled) setItems(res.data)
        })
        .catch(() => {
          // Silent — a failed poll shouldn't interrupt whatever the user is doing; it just retries next interval.
        })
    }
    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [organizationId])

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  const goTo = (path: string) => {
    setOpen(false)
    navigate(path)
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative rounded-md border border-line px-2 py-1 text-sm hover:bg-bg"
        title="Your inbox"
      >
        🔔
        {items.length > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
            {items.length > 99 ? '99+' : items.length}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-96 rounded-lg border border-line bg-surface shadow-lg">
          <div className="border-b border-line px-3 py-2 text-xs font-medium uppercase tracking-wide text-ink-soft">
            Your inbox
          </div>
          {items.length === 0 ? (
            <p className="px-3 py-4 text-sm text-ink-soft">Nothing waiting on you right now.</p>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {items.map((item) => (
                <li key={`${item.category}-${item.entity_id}`} className="border-b border-line last:border-b-0">
                  <button
                    onClick={() => goTo(item.link_path)}
                    className="block w-full px-3 py-2 text-left text-sm hover:bg-bg"
                  >
                    <div className="font-medium text-ink">{item.label}</div>
                    {item.detail && <div className="mt-0.5 text-xs text-ink-soft">{item.detail}</div>}
                    <div className="mt-0.5 text-[10px] text-ink-soft">{timeAgo(item.requested_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
