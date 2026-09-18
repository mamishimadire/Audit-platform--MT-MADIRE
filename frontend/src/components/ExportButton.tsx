import { useEffect, useRef, useState } from 'react'
import { exportToCsv, exportToPdf, exportToXlsx, type ExportReport } from '../lib/exportTable'

// Filenames stay ASCII-safe and collision-free across repeated exports of
// the same report within one day, without needing a full timestamp.
function safeStem(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

export function ExportButton({ report }: { report: () => ExportReport }) {
  const [open, setOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [open])

  const run = async (format: 'csv' | 'xlsx' | 'pdf') => {
    setOpen(false)
    setExporting(true)
    try {
      const r = report()
      const stem = safeStem(r.title)
      if (format === 'csv') exportToCsv(`${stem}.csv`, r)
      else if (format === 'xlsx') await exportToXlsx(`${stem}.xlsx`, r)
      else await exportToPdf(`${stem}.pdf`, r)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={exporting}
        className="rounded-md border border-line px-2 py-1 text-xs font-medium text-ink hover:bg-bg disabled:opacity-60"
      >
        {exporting ? 'Exporting…' : 'Export ▾'}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-40 rounded-md border border-line bg-surface shadow-lg">
          {(
            [
              ['csv', 'CSV'],
              ['xlsx', 'Excel (.xlsx)'],
              ['pdf', 'PDF'],
            ] as const
          ).map(([format, label]) => (
            <button
              key={format}
              onClick={() => run(format)}
              className="block w-full px-3 py-2 text-left text-xs text-ink hover:bg-bg"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
