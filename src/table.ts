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

  // Extract unique states from names (e.g., "Portland, Oregon" → "Oregon")
  const stateSet = new Set<string>()
  for (const r of rows) {
    const parts = r.name.split(', ')
    if (parts.length >= 2) stateSet.add(parts[parts.length - 1])
  }
  const stateList = Array.from(stateSet).sort()

  let sortKey: SortKey = 'avg_nwi'
  let sortDir: SortDir = 'desc'
  let filter = ''
  let minPop = 0
  let stateFilter = ''

  const wrapper = document.createElement('div')
  wrapper.className = 'table-wrapper'

  // Toolbar: search + export
  const toolbar = document.createElement('div')
  toolbar.className = 'table-toolbar'

  const search = document.createElement('input')
  search.type = 'text'
  search.placeholder = 'Search by name...'
  search.className = 'table-search'
  search.addEventListener('input', () => {
    filter = search.value.toLowerCase()
    visibleCount = PAGE_SIZE
    render()
  })
  toolbar.appendChild(search)

  // State filter
  if (stateList.length > 1) {
    const stateSelect = document.createElement('select')
    stateSelect.className = 'table-filter-select'
    stateSelect.innerHTML = '<option value="">All states</option>' +
      stateList.map(s => `<option value="${s}">${s}</option>`).join('')
    stateSelect.addEventListener('change', () => {
      stateFilter = stateSelect.value
      visibleCount = PAGE_SIZE
      render()
    })
    toolbar.appendChild(stateSelect)
  }

  // Population filter
  const popSelect = document.createElement('select')
  popSelect.className = 'table-filter-select'
  popSelect.innerHTML = `
    <option value="0">All populations</option>
    <option value="1000">Pop. 1,000+</option>
    <option value="10000">Pop. 10,000+</option>
    <option value="50000">Pop. 50,000+</option>
    <option value="100000">Pop. 100,000+</option>
  `
  popSelect.addEventListener('change', () => {
    minPop = parseInt(popSelect.value)
    visibleCount = PAGE_SIZE
    render()
  })
  toolbar.appendChild(popSelect)

  const exportBtn = document.createElement('button')
  exportBtn.className = 'table-export-btn'
  exportBtn.textContent = 'Export CSV'
  exportBtn.addEventListener('click', () => exportCsv(getFiltered(), sortKey, sortDir))
  toolbar.appendChild(exportBtn)

  wrapper.appendChild(toolbar)

  // Single table in a scroll wrapper
  const scrollDiv = document.createElement('div')
  scrollDiv.className = 'table-scroll'
  const table = document.createElement('table')
  table.className = 'data-table'
  scrollDiv.appendChild(table)
  wrapper.appendChild(scrollDiv)

  const PAGE_SIZE = 100
  let visibleCount = PAGE_SIZE

  // Status line below toolbar
  const status = document.createElement('div')
  status.className = 'table-status'
  toolbar.appendChild(status)

  function getFiltered(): JurisdictionRow[] {
    let filtered = rows
    if (filter) filtered = filtered.filter(r => r.name.toLowerCase().includes(filter))
    if (minPop > 0) filtered = filtered.filter(r => r.population >= minPop)
    if (stateFilter) filtered = filtered.filter(r => r.name.endsWith(`, ${stateFilter}`))
    return filtered
  }

  function render() {
    let filtered = getFiltered()

    // Sort
    filtered.sort((a, b) => {
      let va: any = a[sortKey]
      let vb: any = b[sortKey]
      if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb as string).toLowerCase() }
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })

    const visible = filtered.slice(0, visibleCount)
    const totalCount = filtered.length

    // Status
    if (totalCount > visibleCount) {
      status.textContent = `Showing ${visibleCount.toLocaleString()} of ${totalCount.toLocaleString()}`
    } else {
      status.textContent = `${totalCount.toLocaleString()} result${totalCount !== 1 ? 's' : ''}`
    }

    let html = '<thead><tr>'
    html += `<th class="sortable th-rank" data-key="avg_nwi">#</th>`
    html += `<th class="sortable th-name" data-key="name">Name</th>`
    html += `<th class="sortable th-pop" data-key="population">Pop.</th>`
    html += `<th class="sortable th-nwi" data-key="avg_nwi">Avg WI</th>`
    html += `<th class="th-dist">Distribution</th>`
    html += '</tr></thead><tbody>'

    visible.forEach((row, i) => {
      const total = row.population || 1
      const pcts = [
        row.least / total * 100,
        row.below / total * 100,
        row.above / total * 100,
        row.most / total * 100,
      ]

      const rank = sortKey !== 'name'
        ? (sortDir === 'desc' ? i + 1 : totalCount - i)
        : ''

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

  // Scroll to load more
  scrollDiv.addEventListener('scroll', () => {
    const { scrollTop, scrollHeight, clientHeight } = scrollDiv
    if (scrollHeight - scrollTop - clientHeight < 200) {
      if (visibleCount < getFiltered().length) {
        visibleCount += PAGE_SIZE
        render()
      }
    }
  })

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

function exportCsv(filtered: JurisdictionRow[], sortKey: SortKey, sortDir: SortDir) {
  filtered = [...filtered]
  filtered.sort((a, b) => {
    let va: any = a[sortKey]
    let vb: any = b[sortKey]
    if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb as string).toLowerCase() }
    if (va < vb) return sortDir === 'asc' ? -1 : 1
    if (va > vb) return sortDir === 'asc' ? 1 : -1
    return 0
  })

  const header = 'FIPS,Name,Population,Avg Walkability Index,Least Walkable Pop,Below Average Pop,Above Average Pop,Most Walkable Pop'
  const csvRows = filtered.map(r => {
    const name = r.name.includes(',') ? `"${r.name}"` : r.name
    return `${r.fips},${name},${r.population},${r.avg_nwi.toFixed(2)},${r.least},${r.below},${r.above},${r.most}`
  })

  const blob = new Blob([header + '\n' + csvRows.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'walkability-data.csv'
  a.click()
  URL.revokeObjectURL(url)
}
