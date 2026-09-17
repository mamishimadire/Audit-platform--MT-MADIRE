import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from './AuthContext'

export function ProtectedRoute() {
  const { user, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return <div className="flex h-screen items-center justify-center text-ink-soft">Loading…</div>
  }
  if (!user) {
    return <Navigate to="/login" replace />
  }
  // A password older than 30 days forces every other page to redirect here
  // until it's changed — /profile itself must stay reachable, or there'd be
  // no way to actually fix it.
  if (user.must_change_password && location.pathname !== '/profile') {
    return <Navigate to="/profile" replace />
  }
  return <Outlet />
}
