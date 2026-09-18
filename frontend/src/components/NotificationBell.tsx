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

type CategoryGroup = 'approval' | 'exception' | 'evidence'

const CATEGORY_GROUP: Record<string, CategoryGroup> = {
  rule: 'approval',
  control_activation: 'approval',
  control_deactivation: 'approval',
  mapping: 'approval',
  data_connection_change: 'approval',
  device_policy_change: 'approval',
  approved_software: 'approval',
  device_revocation: 'approval',
  device_deletion: 'approval',
  monitoring_schedule: 'approval',
  user_approval: 'approval',
  your_exception: 'exception',
  evidence_request: 'evidence',
  evidence_received: 'evidence',
}

const GROUP_ICON: Record<CategoryGroup, string> = {
  approval: '✅',
  exception: '⚠️',
  evidence: '📄',
}

const FILTERS: { key: 'all' | CategoryGroup; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'approval', label: 'Approvals' },
  { key: 'exception', label: 'Your exceptions' },
  { key: 'evidence', label: 'Evidence' },
]

function CategoryIcon({ category }: { category: string }) {
  const group = CATEGORY_GROUP[category] ?? 'approval'
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-bg text-sm" aria-hidden>
      {GROUP_ICON[group]}
    </span>
  )
}

export function NotificationBell({ organizationId }: { organizationId: string | null }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<PendingApprovalOut[]>([])
  const [history, setHistory] = useState<PendingApprovalOut[]>([])
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<'current' | 'history'>('current')
  const [filter, setFilter] = useState<'all' | CategoryGroup>('all')
  const [clearing, setClearing] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const loadCurrent = (orgId: string) =>
    apiClient
      .get<PendingApprovalOut[]>(`/organizations/${orgId}/pending-approvals`)
      .then((res) => setItems(res.data))
      .catch(() => {
        // Silent — a failed poll shouldn't interrupt whatever the user is doing; it just retries next interval.
      })

  const loadHistory = (orgId: string) =>
    apiClient
      .get<PendingApprovalOut[]>(`/organizations/${orgId}/notifications/history`)
      .then((res) => setHistory(res.data))
      .catch(() => {})

  useEffect(() => {
    if (!organizationId) {
      setItems([])
      setHistory([])
      return
    }
    let cancelled = false
    const poll = () => {
      if (!cancelled) loadCurrent(organizationId)
    }
    poll()
    const interval = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [organizationId])

  useEffect(() => {
    if (open && tab === 'history' && organizationId) loadHistory(organizationId)
  }, [open, tab, organizationId])

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

  const dismiss = async (item: PendingApprovalOut, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!organizationId) return
    setItems((prev) => prev.filter((i) => !(i.category === item.category && i.entity_id === item.entity_id)))
    await apiClient.post(`/organizations/${organizationId}/notifications/dismiss`, item).catch(() => {})
  }

  const clearAll = async () => {
    if (!organizationId || items.length === 0) return
    setClearing(true)
    try {
      await apiClient.post(`/organizations/${organizationId}/notifications/clear-all`)
      setItems([])
    } finally {
      setClearing(false)
    }
  }

  const list = tab === 'current' ? items : history
  const filtered = filter === 'all' ? list : list.filter((i) => (CATEGORY_GROUP[i.category] ?? 'approval') === filter)

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
        <div className="absolute right-0 z-20 mt-2 w-[26rem] rounded-lg border border-line bg-surface shadow-lg">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <div className="flex gap-1">
              {(['current', 'history'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`rounded-md px-2 py-1 text-xs font-medium ${
                    tab === t ? 'bg-accent-soft text-accent-ink' : 'text-ink-soft hover:bg-bg'
                  }`}
                >
                  {t === 'current' ? 'Current' : 'History'}
                </button>
              ))}
            </div>
            {tab === 'current' && items.length > 0 && (
              <button
                onClick={clearAll}
                disabled={clearing}
                className="text-xs font-medium text-ink-soft hover:text-ink hover:underline disabled:opacity-60"
              >
                {clearing ? 'Clearing…' : 'Clear all'}
              </button>
            )}
          </div>

          <div className="flex gap-1 border-b border-line px-3 py-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                  filter === f.key ? 'bg-ink text-white' : 'bg-bg text-ink-soft hover:bg-line'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <p className="px-3 py-4 text-sm text-ink-soft">
              {tab === 'current' ? 'Nothing waiting on you right now.' : 'No cleared notifications yet.'}
            </p>
          ) : (
            <ul className="max-h-96 overflow-y-auto">
              {filtered.map((item) => (
                <li key={`${item.category}-${item.entity_id}`} className="group border-b border-line last:border-b-0">
                  <button onClick={() => goTo(item.link_path)} className="flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-bg">
                    <CategoryIcon category={item.category} />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-ink">{item.label}</div>
                      {item.detail && <div className="mt-0.5 text-xs text-ink-soft">{item.detail}</div>}
                      <div className="mt-0.5 text-[10px] text-ink-soft">{timeAgo(item.requested_at)}</div>
                    </div>
                    {tab === 'current' && (
                      <span
                        onClick={(e) => dismiss(item, e)}
                        title="Clear this notification"
                        className="shrink-0 rounded-full px-1.5 py-0.5 text-xs text-ink-soft opacity-0 hover:bg-line hover:text-ink group-hover:opacity-100"
                      >
                        ✕
                      </span>
                    )}
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
