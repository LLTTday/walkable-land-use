/**
 * Sortable, filterable table view for jurisdiction data.
 */

interface JurisdictionRow {
  fips: string
  name: string
  population: number
  least: number
  below: number
  above: number
  most: number
  avg_nwi: number
}

type SortKey = keyof JurisdictionRow
type SortDir = 'asc' | 'desc'

const NWI_COLORS = ['#e84830', '#e8b830', '#7ebf6e', '#3d6b35']

export function buildTable(
  container: HTMLElement,
  data: Record<string, { name: string; population: number; avg_nwi: number; by_nwi: Record<string, { population: number }> }>,
  onRowClick?: (fips: string) => void,
) {
  // Transform data into rows
  const rows: JurisdictionRow[] = Object.entries(data).map(([fips, j]) => ({
    fips,
    name: j.name,
    population: j.population,
    least: j.by_nwi['0']?.population || 0,
    below: j.by_nwi['1']?.population || 0,
    above: j.by_nwi['2']?.population || 0,
    most: j.by_nwi['3']?.population || 0,
    avg_nwi: j.avg_nwi,
  }))

  let sortKey: SortKey = 'avg_nwi'
  let sortDir: SortDir = 'desc'
  let filter = ''

  const wrapper = document.createElement('div')
  wrapper.className = 'table-wrapper'

  // Search input
  const search = document.createElement('input')
  search.type = 'text'
  search.placeholder = 'Search by name...'
  search.className = 'table-search'
  search.addEventListener('input', () => {
    filter = search.value.toLowerCase()
    render()
  })
  wrapper.appendChild(search)

  // Single table in a scroll wrapper
  const scrollDiv = document.createElement('div')
  scrollDiv.className = 'table-scroll'
  const table = document.createElement('table')
  table.className = 'data-table'
  scrollDiv.appendChild(table)
  wrapper.appendChild(scrollDiv)

  function render() {
    // Filter
    let filtered = rows
    if (filter) {
      filtered = rows.filter(r => r.name.toLowerCase().includes(filter))
    }

    // Sort
    filtered.sort((a, b) => {
      let va: any = a[sortKey]
      let vb: any = b[sortKey]
      if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb as string).toLowerCase() }
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })

    let html = '<thead><tr>'
    html += `<th class="sortable th-rank" data-key="avg_nwi">#</th>`
    html += `<th class="sortable th-name" data-key="name">Name</th>`
    html += `<th class="sortable th-pop" data-key="population">Pop.</th>`
    html += `<th class="sortable th-nwi" data-key="avg_nwi">Avg WI</th>`
    html += `<th class="th-dist">Distribution</th>`
    html += '</tr></thead><tbody>'

    filtered.forEach((row, i) => {
      const total = row.population || 1
      const pcts = [
        row.least / total * 100,
        row.below / total * 100,
        row.above / total * 100,
        row.most / total * 100,
      ]

      const rank = sortKey === 'avg_nwi' && sortDir === 'desc' ? i + 1 : ''

      html += `<tr data-fips="${row.fips}">`
      html += `<td class="cell-rank">${rank}</td>`
      html += `<td class="cell-name">${row.name}</td>`
      html += `<td class="cell-pop">${formatNum(row.population)}</td>`
      html += `<td class="cell-nwi">${row.avg_nwi.toFixed(1)}</td>`
      html += `<td class="cell-dist"><div class="mini-bar">`
      for (let j = 0; j < 4; j++) {
        if (pcts[j] > 0) {
          html += `<div class="mini-bar-seg" style="width:${pcts[j]}%;background:${NWI_COLORS[j]}"></div>`
        }
      }
      html += `</div></td></tr>`
    })

    html += '</tbody>'
    table.innerHTML = html

    // Attach sort handlers
    table.querySelectorAll('th.sortable').forEach(th => {
      const el = th as HTMLElement
      const key = el.dataset.key as SortKey
      // Mark current sort
      el.classList.toggle('sorted', key === sortKey)
      el.setAttribute('data-dir', key === sortKey ? sortDir : '')
      el.addEventListener('click', () => {
        if (sortKey === key) {
          sortDir = sortDir === 'asc' ? 'desc' : 'asc'
        } else {
          sortKey = key
          sortDir = key === 'name' ? 'asc' : 'desc'
        }
        render()
      })
    })

    // Row click
    if (onRowClick) {
      table.querySelectorAll('tbody tr').forEach(tr => {
        const el = tr as HTMLElement
        el.style.cursor = 'pointer'
        el.addEventListener('click', () => {
          onRowClick(el.dataset.fips!)
        })
      })
    }
  }

  render()
  container.innerHTML = ''
  container.appendChild(wrapper)

  return { refresh: render }
}

function formatNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return Math.round(n / 1_000).toLocaleString() + 'k'
  return n.toLocaleString()
}
