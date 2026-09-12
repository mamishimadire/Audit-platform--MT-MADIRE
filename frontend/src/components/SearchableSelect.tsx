import { useEffect, useRef, useState } from 'react'

export interface SearchableSelectOption {
  value: string
  label: string
  hint?: string
}

/**
 * A plain <select> is unusable once a data source has more than a couple
 * dozen discovered tables (the 130-collection MongoDB demo data source
 * being the concrete case this was built for) — there's no way to search
 * it, only scroll. This is a minimal, dependency-free replacement: a text
 * input that filters the option list as you type, closes on selection or
 * on clicking outside, and still behaves like a controlled `value`/
 * `onChange` pair so it drops into any existing <select> call site.
 */
export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = 'Select…',
  disabled = false,
}: {
  value: string
  onChange: (value: string) => void
  options: SearchableSelectOption[]
  placeholder?: string
  disabled?: boolean
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const selected = options.find((o) => o.value === value)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
        setQuery('')
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const filtered = query.trim()
    ? options.filter((o) => o.label.toLowerCase().includes(query.trim().toLowerCase()))
    : options

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        disabled={disabled}
        value={open ? query : (selected?.label ?? '')}
        onFocus={() => {
          setOpen(true)
          setQuery('')
        }}
        onChange={(e) => {
          setQuery(e.target.value)
          if (!open) setOpen(true)
        }}
        placeholder={selected ? undefined : placeholder}
        className="w-full rounded-md border border-line px-2 py-1 text-xs disabled:opacity-60"
      />
      {open && !disabled && (
        <div className="absolute z-20 mt-1 max-h-56 w-full min-w-[14rem] overflow-y-auto rounded-md border border-line bg-surface shadow-lg">
          {filtered.length === 0 && <div className="px-2 py-1.5 text-xs text-ink-soft">No matches.</div>}
          {filtered.map((o) => (
            <button
              key={o.value}
              type="button"
              onMouseDown={(e) => e.preventDefault()} // keep focus so the click isn't lost to the blur/outside-click handler
              onClick={() => {
                onChange(o.value)
                setOpen(false)
                setQuery('')
              }}
              className={`flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-xs hover:bg-bg ${
                o.value === value ? 'bg-accent-soft text-accent-ink' : 'text-ink'
              }`}
            >
              <span className="truncate font-mono">{o.label}</span>
              {o.hint && <span className="shrink-0 whitespace-nowrap text-[10px] font-medium text-accent-ink">{o.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
