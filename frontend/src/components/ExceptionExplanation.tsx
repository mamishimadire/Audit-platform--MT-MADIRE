/** The plain-English "what happened / why it matters / what to do" block —
 * shared by Exceptions, Executions, and Findings so the same explanation
 * reads identically wherever an exception shows up. */
export function ExceptionExplanationBlock({
  summary,
  whyItMatters,
  whatToDo,
}: {
  summary: string
  whyItMatters: string
  whatToDo: string
}) {
  return (
    <div className="space-y-2 rounded-md border border-line bg-surface p-3">
      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">What happened</div>
        <p className="mt-1 text-sm text-ink">{summary}</p>
      </div>
      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-ink-soft">Why it matters (the risk)</div>
        <p className="mt-1 text-sm text-ink">{whyItMatters}</p>
      </div>
      <div className="rounded-md border border-accent-soft bg-accent-soft/40 p-2">
        <div className="text-xs font-medium uppercase tracking-wide text-accent-ink">What to do (the recommendation)</div>
        <p className="mt-1 text-sm text-ink">{whatToDo}</p>
      </div>
    </div>
  )
}
