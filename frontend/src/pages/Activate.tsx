import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiClient, setStoredToken } from '../lib/apiClient'

export function ActivatePage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [temporaryPassword, setTemporaryPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      const response = await apiClient.post<{ access_token: string }>('/auth/activate', {
        email,
        temporary_password: temporaryPassword,
        new_password: newPassword,
      })
      setStoredToken(response.data.access_token)
      navigate('/')
      window.location.reload() // ensure AuthProvider picks up the new session
    } catch {
      setError('That email/temporary password combination is not valid or has already been used.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg">
      <form onSubmit={handleSubmit} className="w-full max-w-sm rounded-lg border border-line bg-surface p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-ink">Activate your account</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Enter the one-time temporary password you were given, and choose a real password.
        </p>

        <label className="mt-6 block text-sm font-medium text-ink">Email</label>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        <label className="mt-4 block text-sm font-medium text-ink">Temporary password</label>
        <input
          type="password"
          required
          value={temporaryPassword}
          onChange={(e) => setTemporaryPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        <label className="mt-4 block text-sm font-medium text-ink">New password (12+ characters)</label>
        <input
          type="password"
          required
          minLength={12}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-6 w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {isSubmitting ? 'Activating…' : 'Activate & sign in'}
        </button>
      </form>
    </div>
  )
}
