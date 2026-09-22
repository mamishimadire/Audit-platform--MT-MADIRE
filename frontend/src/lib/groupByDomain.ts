/**
 * Two-level grouping shared by Exceptions and Findings: audit domain/
 * category at the top (matching how the Controls page groups its own
 * rows), the individual control within it — the level each page already
 * grouped by before this existed. "Uncategorized" at either level is a
 * real, rare case (a manually-created audit test with no control library
 * link), shown honestly rather than hidden; when both levels would say it
 * for the same item, the inner header is skipped so the word doesn't
 * repeat for no reason.
 */
export interface ControlGroup<T> {
  label: string
  code: string
  items: T[]
}

export interface DomainGroup<T> {
  domain: string
  controls: ControlGroup<T>[]
  count: number
}

export function groupByDomainAndControl<T>(
  items: T[],
  controlCode: (item: T) => string | null,
  controlName: (item: T) => string | null,
  domainByCode: Map<string, string>
): DomainGroup<T>[] {
  const byDomain = new Map<string, Map<string, ControlGroup<T>>>()
  for (const item of items) {
    const code = controlCode(item)
    const domain = (code ? domainByCode.get(code) : undefined) ?? 'Uncategorized'
    const controlLabel = code ? `${code} — ${controlName(item)}` : 'Uncategorized'
    const controlKey = code ?? '￿' // sorts after every real code

    let controls = byDomain.get(domain)
    if (!controls) {
      controls = new Map()
      byDomain.set(domain, controls)
    }
    let group = controls.get(controlKey)
    if (!group) {
      group = { label: controlLabel, code: controlKey, items: [] }
      controls.set(controlKey, group)
    }
    group.items.push(item)
  }

  return [...byDomain.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([domain, controls]) => {
      const list = [...controls.values()].sort((a, b) => a.code.localeCompare(b.code))
      return { domain, controls: list, count: list.reduce((n, g) => n + g.items.length, 0) }
    })
}
