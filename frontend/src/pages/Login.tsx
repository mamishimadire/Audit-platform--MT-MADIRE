import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function LoginPage() {
  const { login, user, isLoading, authMessage, clearAuthMessage } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // isLoading covers the brief window where AuthContext is still checking a
  // stored token via /auth/me — without it, a duplicated tab could show a
  // blank, submittable login form for that instant even though this
  // browser is (or is about to turn out to be) already signed in. Same
  // loading UI as ProtectedRoute, for the same reason.
  if (isLoading) return <div className="flex h-screen items-center justify-center text-ink-soft">Loading…</div>
  if (user) return <Navigate to="/" replace />

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      await login(email, password)
      navigate('/')
    } catch (err: any) {
      if (err?.response?.status === 401) {
        setError('Incorrect email or password.')
      } else if (!err?.response) {
        // No response at all — network failure, backend down, or a CORS
        // block — genuinely different from a wrong password, and telling
        // them apart saves a lot of confused re-typing of a correct password.
        setError("Could not reach the platform — check your connection, or the platform may be temporarily unavailable.")
      } else {
        setError('Something went wrong signing in. Please try again.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg">
      <form onSubmit={handleSubmit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink">Audit Platform</h1>
        <p className="mt-1 text-sm text-ink-soft">Sign in to continue.</p>

        {authMessage && (
          <p className="mt-3 rounded-md border border-line bg-bg px-3 py-2 text-sm text-ink-soft">
            {authMessage}{' '}
            <button type="button" onClick={clearAuthMessage} className="font-medium text-accent-ink hover:underline">
              Dismiss
            </button>
          </p>
        )}

        <label className="mt-6 block text-sm font-medium text-ink">Email</label>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        <label className="mt-4 block text-sm font-medium text-ink">Password</label>
        <input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-6 w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {isSubmitting ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="mt-4 text-center text-xs text-ink-soft">
          First login with a temporary password?{' '}
          <a href="/activate" className="font-medium text-accent-ink hover:underline">
            Activate your account
          </a>
        </p>
      </form>
    </div>
  )
}
