interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
  // When set, a required reason textarea is shown and Confirm stays
  // disabled until it's non-empty — the caller owns the value (so it can
  // reset it, validate it, or send it in its own onConfirm), this component
  // just won't let a confirm through empty.
  reasonRequired?: boolean
  reasonValue?: string
  onReasonChange?: (value: string) => void
  reasonPlaceholder?: string
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  danger = false,
  onConfirm,
  onCancel,
  reasonRequired = false,
  reasonValue = '',
  onReasonChange,
  reasonPlaceholder = 'Reason (required)…',
}: ConfirmDialogProps) {
  if (!open) return null
  const confirmDisabled = reasonRequired && !reasonValue.trim()

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={onCancel}>
      <div
        role="alertdialog"
        aria-modal="true"
        className="w-full max-w-sm rounded-lg border border-line bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-semibold text-ink">{title}</div>
        <p className="mt-2 text-sm text-ink-soft">{message}</p>
        {reasonRequired && (
          <textarea
            autoFocus
            value={reasonValue}
            onChange={(e) => onReasonChange?.(e.target.value)}
            placeholder={reasonPlaceholder}
            rows={3}
            className="mt-3 w-full rounded-md border border-line px-2 py-1.5 text-sm"
          />
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onCancel} className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink hover:bg-bg">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={confirmDisabled}
            className={`rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50 ${danger ? 'bg-red-600 hover:bg-red-700' : 'bg-accent'}`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
