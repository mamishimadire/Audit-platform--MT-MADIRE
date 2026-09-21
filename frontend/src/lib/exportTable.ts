export interface ExportColumn {
  key: string
  label: string
}

export interface ExportReport {
  // Shown as the document title (PDF) / sheet name (XLSX) — keep it
  // short, this is what the exported file is actually about.
  title: string
  // The scope filters currently applied, in plain English (e.g. "Bidvest
  // ALICE · This week (2026-09-11 to 2026-09-18) · Entity: exceptions")
  // — so the report is self-describing without needing this app open.
  subtitle: string
  columns: ExportColumn[]
  rows: Record<string, string | number | null | undefined>[]
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function cell(value: string | number | null | undefined): string {
  return value === null || value === undefined ? '' : String(value)
}

// A CSV cell that starts with = + - @ (or a tab / carriage return) is run as a FORMULA when the file is opened in
// Excel or Sheets, and the data in an export comes from client systems, uploaded files and third-party APIs that
// nobody at this platform controls (=HYPERLINK("http://…") leaks, =cmd|… runs). A leading apostrophe makes it plain
// text. A value that is only a number is left alone, so amounts such as -1250.50 stay numbers.
export function neutralizeFormula(value: string): string {
  return /^[=+\-@\t\r]/.test(value) && !/^-?\d+(\.\d+)?$/.test(value) ? `'${value}` : value
}

export function exportToCsv(filename: string, report: ExportReport) {
  const escape = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const lines = [
    report.columns.map((c) => escape(c.label)).join(','),
    ...report.rows.map((row) => report.columns.map((c) => escape(neutralizeFormula(cell(row[c.key])))).join(',')),
  ]
  // UTF-8 BOM so Excel opens accented/non-ASCII characters correctly.
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' })
  downloadBlob(blob, filename)
}

export async function exportToXlsx(filename: string, report: ExportReport) {
  // Lazy-loaded — exceljs is only needed on the rare click of "Export",
  // not worth shipping in the app's main bundle.
  const ExcelJS = (await import('exceljs')).default
  const workbook = new ExcelJS.Workbook()
  workbook.creator = 'MT AUDIT'
  workbook.created = new Date()
  const sheet = workbook.addWorksheet(report.title.slice(0, 31) || 'Report')

  sheet.addRow([report.title])
  sheet.getCell('A1').font = { bold: true, size: 14 }
  sheet.addRow([report.subtitle])
  sheet.getCell('A2').font = { italic: true, color: { argb: 'FF666666' } }
  sheet.addRow([])

  const headerRow = sheet.addRow(report.columns.map((c) => c.label))
  headerRow.eachCell((c) => {
    c.font = { bold: true, color: { argb: 'FFFFFFFF' } }
    c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF334155' } }
  })

  for (const row of report.rows) {
    sheet.addRow(report.columns.map((c) => cell(row[c.key])))
  }

  sheet.columns.forEach((col, i) => {
    const label = report.columns[i]?.label ?? ''
    const maxDataLen = report.rows.reduce((max, row) => Math.max(max, cell(row[report.columns[i]?.key ?? '']).length), 0)
    col.width = Math.min(60, Math.max(label.length, maxDataLen, 10) + 2)
  })

  const buffer = await workbook.xlsx.writeBuffer()
  downloadBlob(new Blob([buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), filename)
}

export async function exportToPdf(filename: string, report: ExportReport) {
  // Lazy-loaded — jspdf pulls in html2canvas as a transitive dependency,
  // which is only used for image-based PDFs (not this table-based one)
  // but is heavy enough to not belong in the main app bundle either way.
  const [{ jsPDF }, { default: autoTable }] = await Promise.all([import('jspdf'), import('jspdf-autotable')])
  const doc = new jsPDF({ orientation: report.columns.length > 5 ? 'landscape' : 'portrait' })
  doc.setFontSize(14)
  doc.text(report.title, 14, 15)
  doc.setFontSize(9)
  doc.setTextColor(100)
  doc.text(report.subtitle, 14, 21)

  autoTable(doc, {
    startY: 26,
    head: [report.columns.map((c) => c.label)],
    body: report.rows.map((row) => report.columns.map((c) => cell(row[c.key]))),
    styles: { fontSize: 8, cellPadding: 2 },
    headStyles: { fillColor: [51, 65, 85] },
    margin: { top: 26 },
  })

  doc.save(filename)
}
