import { useState, type FormEvent } from 'react'
import { apiClient } from '../lib/apiClient'
import { useAuth } from '../auth/AuthContext'

export function ProfilePage() {
  const { user, refreshUser } = useAuth()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (!user) return null

  const mustChange = user.must_change_password ?? false
  const reminderDays = user.password_reminder_days_remaining ?? null

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSuccess(false)
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.')
      return
    }
    setIsSubmitting(true)
    try {
      await apiClient.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSuccess(true)
      await refreshUser()
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? 'Could not change your password.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="max-w-lg">
      <h1 className="text-lg font-semibold text-ink">Your profile</h1>
      <p className="mt-1 text-sm text-ink-soft">
        {user.first_name} {user.last_name} · {user.email}
      </p>

      {mustChange && (
        <div className="mt-4 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          Your password is more than 30 days old and must be changed before you can continue using the platform.
        </div>
      )}
      {!mustChange && reminderDays !== null && (
        <div className="mt-4 rounded-md border border-orange-300 bg-orange-50 px-3 py-2 text-sm text-orange-800">
          Your password will expire in {reminderDays} day{reminderDays === 1 ? '' : 's'} — change it soon to avoid being
          locked out of other pages.
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-6 rounded-lg border border-line bg-surface p-5">
        <h2 className="text-sm font-semibold text-ink">Change password</h2>
        <p className="mt-1 text-xs text-ink-soft">
          Passwords must be changed at least every 30 days. You'll need your current password to set a new one.
        </p>

        <label className="mt-4 block text-sm font-medium text-ink">Current password</label>
        <input
          type="password"
          required
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        <label className="mt-4 block text-sm font-medium text-ink">New password</label>
        <input
          type="password"
          required
          minLength={12}
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <p className="mt-1 text-xs text-ink-soft">At least 12 characters.</p>

        <label className="mt-4 block text-sm font-medium text-ink">Confirm new password</label>
        <input
          type="password"
          required
          minLength={12}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-accent"
        />

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
        {success && <p className="mt-3 text-sm text-accent-ink">Password changed.</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-5 rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {isSubmitting ? 'Changing…' : 'Change password'}
        </button>
      </form>
    </div>
  )
}
