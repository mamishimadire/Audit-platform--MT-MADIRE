import type { TrendPoint } from '../types/api'

interface Props {
  title: string
  points: TrendPoint[]
  color?: string
}

export function TrendChart({ title, points, color = 'var(--color-accent)' }: Props) {
  const max = Math.max(1, ...points.map((p) => p.count))
  const width = 320
  const height = 64
  const barWidth = width / points.length
  const total = points.reduce((sum, p) => sum + p.count, 0)

  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-soft">{title}</span>
        <span className="text-sm font-semibold text-ink tabular-nums">{total}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}: ${total} over the last ${points.length} days`} className="mt-2 w-full">
        {points.map((p, i) => {
          const barHeight = (p.count / max) * (height - 14)
          return (
            <g key={p.date}>
              <rect
                x={i * barWidth + 1}
                y={height - 14 - barHeight}
                width={Math.max(1, barWidth - 2)}
                height={barHeight}
                rx={1.5}
                fill={color}
                opacity={p.count === 0 ? 0.15 : 0.85}
              />
            </g>
          )
        })}
        <text x={0} y={height} fontSize="8" fill="currentColor" opacity={0.5}>
          {new Date(points[0].date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
        </text>
        <text x={width} y={height} fontSize="8" textAnchor="end" fill="currentColor" opacity={0.5}>
          {new Date(points[points.length - 1].date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
        </text>
      </svg>
    </div>
  )
}
