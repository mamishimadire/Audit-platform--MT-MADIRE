interface PlaceholderPageProps {
  title: string
  phase: string
  description: string
}

/**
 * Honest stand-in for nav items with no backend yet — never fabricated
 * numbers or fake data, per the build instruction's Section 26 rule.
 */
export function PlaceholderPage({ title, phase, description }: PlaceholderPageProps) {
  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-ink">{title}</h1>
      <div className="mt-4 rounded-lg border border-dashed border-line bg-surface p-6">
        <span className="inline-block rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent-ink">
          {phase}
        </span>
        <p className="mt-3 text-sm text-ink-soft">{description}</p>
      </div>
    </div>
  )
}
