import { useEffect, useState } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'
import { useActiveOrganization } from '../hooks/useActiveOrganization'
import { OrganizationPicker } from '../components/OrganizationPicker'
import { TrendChart } from '../components/TrendChart'
import type { DashboardStats } from '../types/api'

interface StatTileProps {
  label: string
  value: string | number
  tone?: 'default' | 'warning' | 'critical' | 'good'
}

const TONE_STYLES: Record<string, string> = {
  default: 'text-ink',
  good: 'text-accent-ink',
  warning: 'text-orange-600',
  critical: 'text-red-600',
}

function StatTile({ label, value, tone = 'default' }: StatTileProps) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className={`text-2xl font-semibold tabular-nums ${TONE_STYLES[tone]}`}>{value}</div>
      <div className="mt-1 text-xs text-ink-soft">{label}</div>
    </div>
  )
}

function GatewayHealthBar({ stats }: { stats: DashboardStats }) {
  const { online, offline, pending, deregistered } = stats.gateway_health
  const total = online + offline + pending + deregistered
  if (total === 0) {
    return <p className="text-xs text-ink-soft">No gateways registered yet.</p>
  }
  const segments = [
    { label: 'Online', count: online, className: 'bg-accent' },
    { label: 'Offline', count: offline, className: 'bg-red-400' },
    { label: 'Pending', count: pending, className: 'bg-orange-400' },
    { label: 'Deregistered', count: deregistered, className: 'bg-line' },
  ].filter((s) => s.count > 0)

  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded-full">
        {segments.map((s) => (
          <div key={s.label} className={s.className} style={{ width: `${(s.count / total) * 100}%` }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink-soft">
        {segments.map((s) => (
          <span key={s.label}>
            {s.label}: <span className="font-medium text-ink">{s.count}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export function DashboardPage() {
  const { user } = useAuth()
  const { organizationId, setOrganizationId, organizations, needsPicker, isLoading, loadError } = useActiveOrganization()
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [statsError, setStatsError] = useState(false)

  useEffect(() => {
    if (!organizationId) return
    setStats(null)
    setStatsError(false)
    apiClient
      .get<DashboardStats>(`/organizations/${organizationId}/dashboard`)
      .then((res) => setStats(res.data))
      .catch(() => setStatsError(true))
  }, [organizationId])

  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink">Welcome, {user?.first_name}</h1>
      <p className="mt-1 text-sm text-ink-soft">Your audit assurance overview — live data, not placeholders.</p>
      {needsPicker && <OrganizationPicker organizations={organizations} value={organizationId} onChange={setOrganizationId} />}

      {isLoading ? (
        <p className="mt-6 text-sm text-ink-soft">Loading organizations…</p>
      ) : loadError ? (
        <p className="mt-6 text-sm text-red-600">Could not load your organizations. Try refreshing the page.</p>
      ) : needsPicker && organizations.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-line bg-surface p-6 text-sm text-ink-soft">
          You haven't added any client organizations yet.{' '}
          <a href="/organizations" className="font-medium text-accent-ink hover:underline">
            Add your first one
          </a>
          .
        </div>
      ) : statsError ? (
        <p className="mt-6 text-sm text-red-600">Could not load dashboard data for this organization. Try refreshing the page.</p>
      ) : !stats ? (
        <p className="mt-6 text-sm text-ink-soft">Loading dashboard…</p>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatTile label="Active Monitoring Tests" value={stats.active_monitoring_tests} />
            <StatTile label="Tests Executed Today" value={stats.tests_executed_today} />
            <StatTile label="Failed Tests" value={stats.failed_tests} tone={stats.failed_tests > 0 ? 'critical' : 'default'} />
            <StatTile label="Open Exceptions" value={stats.exceptions_open} tone={stats.exceptions_open > 0 ? 'warning' : 'good'} />
            <StatTile label="High-Risk Exceptions" value={stats.exceptions_high_risk} tone={stats.exceptions_high_risk > 0 ? 'critical' : 'good'} />
            <StatTile label="Open Findings" value={stats.open_findings} tone={stats.open_findings > 0 ? 'warning' : 'good'} />
            <StatTile label="Overdue Findings" value={stats.overdue_findings} tone={stats.overdue_findings > 0 ? 'critical' : 'good'} />
            <StatTile label="Remediation Rate" value={`${stats.remediation_rate}%`} tone="good" />
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <TrendChart title="Test executions — last 14 days" points={stats.executions_trend} />
            <TrendChart title="Exceptions detected — last 14 days" points={stats.exceptions_trend} color="#dc2626" />
          </div>

          <div className="mt-6 rounded-lg border border-line bg-surface p-4">
            <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Gateway health</div>
            <div className="mt-2">
              <GatewayHealthBar stats={stats} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}
