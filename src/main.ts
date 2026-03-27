import './style.css'
import maplibregl, { addProtocol } from 'maplibre-gl'
import { Protocol } from 'pmtiles'
import { buildTable } from './table'

// ─── Types ───

interface NwiLevel {
  population: number
  demographics: Record<string, Record<string, number>>
}

interface Jurisdiction {
  name: string
  population: number
  avg_nwi: number
  by_nwi: Record<string, NwiLevel>
}

type DataSet = Record<string, Jurisdiction>

// ─── Constants ───

const NWI_COLORS = ['#e84830', '#e8b830', '#7ebf6e', '#3d6b35']
const _NWI_LABELS = ['Least Walkable', 'Below Average', 'Above Average', 'Most Walkable']
void _NWI_LABELS // used in future panel work

// Choropleth color stops — avg_nwi on 1-10 scale
const CHOROPLETH_STOPS: [number, string][] = [
  [0, '#f0efed'],
  [3.0, '#e84830'],
  [4.0, '#e8a030'],
  [5.0, '#e8d030'],
  [5.5, '#c8d868'],
  [6.0, '#7ebf6e'],
  [7.0, '#5a9a4a'],
  [8.0, '#3d6b35'],
]

// ─── State ───

let currentLevel: 'states' | 'counties' | 'cities' = 'states'
let dataCache: Record<string, DataSet> = {}
let map: maplibregl.Map
let _drillState: string | null = null // state FIPS when drilled into counties
void _drillState
let featureBounds: Record<string, [number, number, number, number]> = {}

// State FIPS → approximate bounds for zoom
const STATE_BOUNDS: Record<string, [[number, number], [number, number]]> = {
  '01': [[-88.5, 30.2], [-84.9, 35.0]], '02': [[-179.1, 51.2], [-129.9, 71.4]],
  '04': [[-114.8, 31.3], [-109.0, 37.0]], '05': [[-94.6, 33.0], [-89.6, 36.5]],
  '06': [[-124.4, 32.5], [-114.1, 42.0]], '08': [[-109.1, 37.0], [-102.0, 41.0]],
  '09': [[-73.7, 41.0], [-71.8, 42.1]], '10': [[-75.8, 38.5], [-75.0, 39.8]],
  '11': [[-77.1, 38.8], [-77.0, 39.0]], '12': [[-87.6, 24.5], [-80.0, 31.0]],
  '13': [[-85.6, 30.4], [-80.8, 35.0]], '15': [[-160.2, 18.9], [-154.8, 22.2]],
  '16': [[-117.2, 42.0], [-111.0, 49.0]], '17': [[-91.5, 37.0], [-87.5, 42.5]],
  '18': [[-88.1, 37.8], [-84.8, 41.8]], '19': [[-96.6, 40.4], [-90.1, 43.5]],
  '20': [[-102.1, 37.0], [-94.6, 40.0]], '21': [[-89.6, 36.5], [-81.9, 39.1]],
  '22': [[-94.0, 29.0], [-89.0, 33.0]], '23': [[-71.1, 43.1], [-67.0, 47.5]],
  '24': [[-79.5, 37.9], [-75.0, 39.7]], '25': [[-73.5, 41.2], [-70.0, 42.9]],
  '26': [[-90.4, 41.7], [-82.4, 48.3]], '27': [[-97.2, 43.5], [-89.5, 49.4]],
  '28': [[-91.7, 30.2], [-88.1, 35.0]], '29': [[-95.8, 36.0], [-89.1, 40.6]],
  '30': [[-116.0, 44.4], [-104.0, 49.0]], '31': [[-104.1, 40.0], [-95.3, 43.0]],
  '32': [[-120.0, 35.0], [-114.0, 42.0]], '33': [[-72.6, 42.7], [-70.7, 45.3]],
  '34': [[-75.6, 38.9], [-73.9, 41.4]], '35': [[-109.1, 32.0], [-103.0, 37.0]],
  '36': [[-79.8, 40.5], [-71.9, 45.0]], '37': [[-84.3, 33.8], [-75.5, 36.6]],
  '38': [[-104.0, 45.9], [-96.6, 49.0]], '39': [[-84.8, 38.4], [-80.5, 42.0]],
  '40': [[-103.0, 33.6], [-94.4, 37.0]], '41': [[-124.6, 42.0], [-116.5, 46.3]],
  '42': [[-80.5, 39.7], [-74.7, 42.3]], '44': [[-71.9, 41.1], [-71.1, 42.0]],
  '45': [[-83.4, 32.0], [-78.5, 35.2]], '46': [[-104.1, 42.5], [-96.4, 46.0]],
  '47': [[-90.3, 35.0], [-81.6, 36.7]], '48': [[-106.6, 25.8], [-93.5, 36.5]],
  '49': [[-114.1, 37.0], [-109.0, 42.0]], '50': [[-73.4, 42.7], [-71.5, 45.0]],
  '51': [[-83.7, 36.5], [-75.2, 39.5]], '53': [[-124.7, 45.5], [-116.9, 49.0]],
  '54': [[-82.6, 37.2], [-77.7, 40.6]], '55': [[-92.9, 42.5], [-86.8, 47.1]],
  '56': [[-111.1, 41.0], [-104.1, 45.0]], '72': [[-67.3, 17.9], [-65.6, 18.5]],
}

// ─── Data loading ───

async function loadData(level: string): Promise<DataSet> {
  if (dataCache[level]) return dataCache[level]
  const resp = await fetch(`/data/${level}.json`)
  const data: DataSet = await resp.json()
  dataCache[level] = data
  return data
}

// ─── Map setup ───

function initMap(): maplibregl.Map {
  const protocol = new Protocol()
  addProtocol('pmtiles', protocol.tile)

  const m = new maplibregl.Map({
    container: 'map',
    style: {
      version: 8,
      sources: {},
      layers: [{
        id: 'background',
        type: 'background',
        paint: { 'background-color': '#f5f4f2' },
      }],
      glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    },
    center: [-98.5, 39.8],
    zoom: 3.5,
    maxBounds: [[-180, 10], [-50, 75]],
  })

  m.addControl(new maplibregl.NavigationControl(), 'top-right')

  m.on('load', () => {
    // Add PMTiles source
    m.addSource('boundaries', {
      type: 'vector',
      url: import.meta.env.DEV
        ? 'pmtiles:///boundaries.pmtiles'
        : 'pmtiles:///tiles/boundaries.pmtiles',
      promoteId: { states: 'FIPS', counties: 'FIPS', places: 'FIPS' },
    })

    // State fill layer
    m.addLayer({
      id: 'states-fill',
      type: 'fill',
      source: 'boundaries',
      'source-layer': 'states',
      paint: {
        'fill-color': '#ddd',
        'fill-opacity': 0.8,
      },
    })

    // State outline
    m.addLayer({
      id: 'states-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'states',
      paint: {
        'line-color': '#fff',
        'line-width': 1,
      },
    })

    // County fill (hidden by default)
    m.addLayer({
      id: 'counties-fill',
      type: 'fill',
      source: 'boundaries',
      'source-layer': 'counties',
      paint: {
        'fill-color': '#ddd',
        'fill-opacity': 0.8,
      },
      layout: { visibility: 'none' },
    })

    // County outline
    m.addLayer({
      id: 'counties-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'counties',
      paint: {
        'line-color': '#fff',
        'line-width': 0.5,
      },
      layout: { visibility: 'none' },
    })

    // Places fill (hidden by default)
    m.addLayer({
      id: 'places-fill',
      type: 'fill',
      source: 'boundaries',
      'source-layer': 'places',
      paint: {
        'fill-color': '#ddd',
        'fill-opacity': 0.8,
      },
      layout: { visibility: 'none' },
    })

    // Places outline
    m.addLayer({
      id: 'places-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'places',
      paint: {
        'line-color': '#fff',
        'line-width': 0.5,
      },
      layout: { visibility: 'none' },
    })

    // ─── Highlight layers driven by feature-state ───
    for (const sl of ['states', 'counties', 'places'] as const) {
      m.addLayer({
        id: `highlight-${sl}`,
        type: 'line',
        source: 'boundaries',
        'source-layer': sl,
        paint: {
          'line-color': '#1a1a1a',
          'line-width': [
            'case',
            ['boolean', ['feature-state', 'selected'], false], 3.5,
            ['boolean', ['feature-state', 'hover'], false], 2,
            0,
          ],
          'line-opacity': [
            'case',
            ['boolean', ['feature-state', 'selected'], false], 0.9,
            ['boolean', ['feature-state', 'hover'], false], 0.6,
            0,
          ],
        },
      })
    }

    // Block group source + layer (separate PMTiles, appears at zoom 7+)
    m.addSource('blockgroups', {
      type: 'vector',
      url: import.meta.env.DEV
        ? 'pmtiles:///blockgroups.pmtiles'
        : 'pmtiles:///tiles/blockgroups.pmtiles',
    })

    m.addLayer({
      id: 'bg-fill',
      type: 'fill',
      source: 'blockgroups',
      'source-layer': 'blockgroups',
      minzoom: 7,
      paint: {
        'fill-color': [
          'step', ['get', 's'],
          '#e8e4e0',
          0.1, '#e84830',
          4.0, '#e8a030',
          5.0, '#e8d030',
          5.5, '#c8d868',
          6.0, '#7ebf6e',
          7.0, '#5a9a4a',
          8.0, '#3d6b35',
        ],
        // Fade in as we zoom past the jurisdiction level
        'fill-opacity': [
          'interpolate', ['linear'], ['zoom'],
          7, 0,
          8, 0.85,
        ],
      },
    })

    m.addLayer({
      id: 'bg-line',
      type: 'line',
      source: 'blockgroups',
      'source-layer': 'blockgroups',
      minzoom: 9,
      paint: {
        'line-color': 'rgba(255,255,255,0.3)',
        'line-width': 0.5,
      },
    })

    // Labels removed — duplicating at multiple zoom levels.
    // TODO: revisit with proper label deduplication or a base map tile source.

    // Load initial data, color the map, show national stats
    setLevel('states')
    fetch('/data/national.json').then(r => r.json()).then(j => showPanel(j))

    // ─── Click handler (single, idle-gated) ───
    let isAnimating = false

    m.on('click', (e) => {
      if (isAnimating) return

      // If block groups are visible and click hit one, let the BG popup handle it
      if (m.getZoom() >= 7) {
        const bgHit = m.queryRenderedFeatures(e.point, { layers: ['bg-fill'] })
        if (bgHit.length) return
      }

      // Query the active layer only
      const activeLayer = currentLevel === 'states' ? 'states-fill'
        : currentLevel === 'counties' ? 'counties-fill'
        : 'places-fill'

      const features = m.queryRenderedFeatures(e.point, { layers: [activeLayer] })
      if (!features.length) return

      const fips = features[0].properties!.FIPS as string

      if (currentLevel === 'states') {
        drillDown(fips)
      } else {
        selectFeature(fips, e.lngLat)
      }
    })

    // ─── Hover via feature-state ───
    let hoveredId: { id: string; layer: string } | null = null

    const sourceLayerMap: Record<string, string> = {
      'states-fill': 'states',
      'counties-fill': 'counties',
      'places-fill': 'places',
    }

    for (const fillLayer of ['states-fill', 'counties-fill', 'places-fill']) {
      const sl = sourceLayerMap[fillLayer]

      m.on('mouseenter', fillLayer, () => { m.getCanvas().style.cursor = 'pointer' })

      m.on('mousemove', fillLayer, (ev) => {
        if (!ev.features?.length) return
        const fid = ev.features[0].properties!.FIPS as string

        // Clear previous hover
        if (hoveredId) {
          m.setFeatureState(
            { source: 'boundaries', sourceLayer: hoveredId.layer, id: hoveredId.id },
            { hover: false }
          )
        }

        hoveredId = { id: fid, layer: sl }
        m.setFeatureState(
          { source: 'boundaries', sourceLayer: sl, id: fid },
          { hover: true }
        )
      })

      m.on('mouseleave', fillLayer, () => {
        m.getCanvas().style.cursor = ''
        if (hoveredId) {
          m.setFeatureState(
            { source: 'boundaries', sourceLayer: hoveredId.layer, id: hoveredId.id },
            { hover: false }
          )
          hoveredId = null
        }
      })
    }

    // ─── Make isAnimating available to interaction functions ───
    ;(window as any).__mapAnimating = {
      get: () => isAnimating,
      set: (v: boolean) => { isAnimating = v },
      gateOnIdle: () => {
        isAnimating = true
        m.once('idle', () => { isAnimating = false })
      },
    }

    // ─── Block group popup (zoom 7+) ───
    let bgPopup: maplibregl.Popup | null = null

    m.on('click', 'bg-fill', (e) => {
      if (isAnimating || !e.features?.length) return
      const props = e.features[0].properties!
      const score = props.s as number
      const pop = props.p as number | undefined
      const geoid = props.g as string | undefined

      // Derive NWI level label from score
      const levelLabel = score >= 6 ? 'Most Walkable'
        : score >= 5 ? 'Above Average'
        : score >= 4 ? 'Below Average'
        : 'Least Walkable'

      // Derive NWI level index for color
      const levelIdx = score >= 6 ? 3 : score >= 5 ? 2 : score >= 4 ? 1 : 0

      let html = '<div class="bg-popup">'
      html += `<div class="bg-popup-score" style="border-left:3px solid ${NWI_COLORS[levelIdx]}">`
      html += `<strong>${score.toFixed(1)}</strong> <span class="bg-popup-label">${levelLabel}</span>`
      html += '</div>'
      if (pop != null) {
        html += `<div class="bg-popup-row">Pop. ${pop.toLocaleString()}</div>`
      }
      if (geoid) {
        html += `<div class="bg-popup-geoid">${geoid}</div>`
      }
      html += '</div>'

      if (bgPopup) bgPopup.remove()
      bgPopup = new maplibregl.Popup({ closeButton: true, maxWidth: '220px' })
        .setLngLat(e.lngLat)
        .setHTML(html)
        .addTo(m)
    })

    // Pointer cursor on block groups at high zoom
    m.on('mouseenter', 'bg-fill', () => { m.getCanvas().style.cursor = 'pointer' })
    m.on('mouseleave', 'bg-fill', () => { m.getCanvas().style.cursor = '' })
  })

  return m
}

// ─── Level switching ───

async function setLevel(level: 'states' | 'counties' | 'cities') {
  currentLevel = level
  const data = await loadData(level)

  // Update nav buttons
  document.querySelectorAll('.geo-btn').forEach(btn => {
    btn.classList.toggle('active', (btn as HTMLElement).dataset.level === level)
  })

  // Hide all fill layers, then show the active one
  const layers: Record<string, { fill: string; line: string; sourceLayer: string }> = {
    states: { fill: 'states-fill', line: 'states-line', sourceLayer: 'states' },
    counties: { fill: 'counties-fill', line: 'counties-line', sourceLayer: 'counties' },
    cities: { fill: 'places-fill', line: 'places-line', sourceLayer: 'places' },
  }

  for (const [, l] of Object.entries(layers)) {
    map.setLayoutProperty(l.fill, 'visibility', 'none')
    map.setLayoutProperty(l.line, 'visibility', 'none')
  }

  const active = layers[level]
  map.setLayoutProperty(active.fill, 'visibility', 'visible')
  map.setLayoutProperty(active.line, 'visibility', 'visible')

  // Keep state outlines visible for context when showing counties or cities
  if (level !== 'states') {
    map.setLayoutProperty('states-line', 'visibility', 'visible')
  }

  // Clear selection highlight
  clearSelection()

  colorMap(data, active.fill, active.sourceLayer)

  // Reset panel
  showEmptyPanel()
}

// ─── Choropleth coloring ───

function colorMap(data: DataSet, layerId: string, _sourceLayer: string) {
  // For small datasets (states, cities), use a simple match expression.
  // For large datasets (counties), embed NWI score as a numeric property
  // in a lookup and use step/interpolate on the score.
  const entries = Object.entries(data)

  // Build a match expression mapping FIPS -> numeric NWI score,
  // then use step to convert score -> color.
  // This is two expressions but keeps the match output as numbers (smaller).
  const scoreMatch: any[] = ['match', ['get', 'FIPS']]
  for (const [fips, j] of entries) {
    scoreMatch.push(fips, j.avg_nwi)
  }
  scoreMatch.push(0) // default: 0 for unmatched

  // Step expression: score -> color
  const colorExpr = [
    'step',
    scoreMatch,
    '#e8e4e0',  // default for score 0
    0.1, '#e84830',   // > 0 (has data but very low)
    4.0, '#e8a030',
    5.0, '#e8d030',
    5.5, '#c8d868',
    6.0, '#7ebf6e',
    7.0, '#5a9a4a',
    8.0, '#3d6b35',
  ]

  console.log(`colorMap: ${entries.length} entries for ${layerId}`)

  map.setPaintProperty(layerId, 'fill-color', colorExpr as any)
}

// interpolateColor kept for potential future use (e.g. custom legend)
void CHOROPLETH_STOPS

// ─── Interaction: drill-down, select, back ───

const anim = () => (window as any).__mapAnimating as {
  get: () => boolean; set: (v: boolean) => void; gateOnIdle: () => void
}

let selectedFeature: { id: string; sourceLayer: string } | null = null

function clearSelection() {
  if (selectedFeature) {
    map.setFeatureState(
      { source: 'boundaries', sourceLayer: selectedFeature.sourceLayer, id: selectedFeature.id },
      { selected: false }
    )
    selectedFeature = null
  }
}

async function drillDown(stateFips: string) {
  _drillState = stateFips
  anim().gateOnIdle() // block clicks until tiles load at new zoom

  // Load data
  const [countyData, stateData] = await Promise.all([
    loadData('counties'),
    loadData('states'),
  ])

  // Switch to county view
  currentLevel = 'counties'
  document.querySelectorAll('.geo-btn').forEach(btn => {
    btn.classList.toggle('active', (btn as HTMLElement).dataset.level === 'counties')
  })

  // Show/hide layers
  map.setLayoutProperty('states-fill', 'visibility', 'none')
  map.setLayoutProperty('counties-fill', 'visibility', 'visible')
  map.setLayoutProperty('counties-line', 'visibility', 'visible')
  map.setLayoutProperty('states-line', 'visibility', 'visible')
  map.setLayoutProperty('places-fill', 'visibility', 'none')
  map.setLayoutProperty('places-line', 'visibility', 'none')

  // Color counties
  colorMap(countyData, 'counties-fill', 'counties')

  // Highlight drilled state
  clearSelection()
  map.setFeatureState(
    { source: 'boundaries', sourceLayer: 'states', id: stateFips },
    { selected: true }
  )
  selectedFeature = { id: stateFips, sourceLayer: 'states' }

  // Zoom to state — fitBounds interrupts any in-progress animation (no stop needed)
  const bounds = STATE_BOUNDS[stateFips]
  if (bounds) {
    map.fitBounds(bounds, { padding: 50, duration: 800 })
  }

  // Panel + UI
  const state = stateData[stateFips]
  if (state) showPanel(state)
  showBackButton(true)
  updateHash('counties', stateFips)
}

function selectFeature(fips: string, _lngLat: maplibregl.LngLat) {
  const level = currentLevel === 'cities' ? 'cities' : 'counties'
  const data = dataCache[level]
  if (!data) return
  const jurisdiction = data[fips]
  if (!jurisdiction) return

  // Highlight via feature-state
  clearSelection()
  const sourceLayer = level === 'cities' ? 'places' : 'counties'
  map.setFeatureState(
    { source: 'boundaries', sourceLayer, id: fips },
    { selected: true }
  )
  selectedFeature = { id: fips, sourceLayer }

  // Zoom — use requestAnimationFrame to ensure a clean frame after any prior animation
  const fb = featureBounds[fips]
  requestAnimationFrame(() => {
    if (fb) {
      map.fitBounds([[fb[0], fb[1]], [fb[2], fb[3]]], { padding: 60, duration: 800, maxZoom: 13 })
    } else {
      map.flyTo({ center: _lngLat, zoom: 9, duration: 800 })
    }
  })

  // Panel
  showPanel(jurisdiction)
  updateHash(level, fips)
}

function drillUp() {
  _drillState = null
  clearSelection()

  // Zoom out, then switch view on idle
  map.flyTo({ center: [-98.5, 39.8], zoom: 3.5, duration: 800 })
  map.once('idle', () => { setLevel('states') })

  showBackButton(false)
  showEmptyPanel()
  updateHash('states')
}

function showBackButton(show: boolean) {
  let btn = document.getElementById('back-btn')
  if (show && !btn) {
    btn = document.createElement('button')
    btn.id = 'back-btn'
    btn.className = 'geo-btn'
    btn.textContent = 'National'
    btn.addEventListener('click', drillUp)
    document.getElementById('geo-nav')!.prepend(btn)
  }
  if (btn) btn.classList.toggle('hidden', !show)
}

// ─── Panel rendering ───

function showEmptyPanel() {
  document.getElementById('panel-empty')!.classList.remove('hidden')
  document.getElementById('panel-content')!.classList.add('hidden')
}

function showPanel(j: Jurisdiction) {
  document.getElementById('panel-empty')!.classList.add('hidden')
  document.getElementById('panel-content')!.classList.remove('hidden')

  document.getElementById('panel-name')!.textContent = j.name

  // Summary stats
  const abovePct = j.population > 0
    ? Math.round(((j.by_nwi['2']?.population || 0) + (j.by_nwi['3']?.population || 0)) / j.population * 100)
    : 0
  document.getElementById('panel-summary')!.innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${formatPop(j.population)}</div>
      <div class="stat-label">Population</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${j.avg_nwi.toFixed(1)}</div>
      <div class="stat-label">Avg Walkability</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${abovePct}%</div>
      <div class="stat-label">Above Avg+</div>
    </div>
  `

  // NWI bar
  renderNwiBar(j)

  // Demographics — with view toggle
  renderDemoPanel(j)
}

function renderNwiBar(j: Jurisdiction) {
  const total = j.population
  if (total === 0) return

  let html = '<div class="nwi-bar-container">'
  for (let i = 0; i < 4; i++) {
    const pop = j.by_nwi[String(i)]?.population || 0
    const pct = pop / total * 100
    if (pct > 0) {
      const label = pct > 8 ? `${Math.round(pct)}%` : ''
      html += `<div class="nwi-bar-segment" data-level="${i}" style="width:${pct}%;background:${NWI_COLORS[i]}">${label}</div>`
    }
  }
  html += '</div>'
  html += '<div class="nwi-bar-labels"><span>Least Walkable</span><span>Most Walkable</span></div>'
  document.getElementById('nwi-bar')!.innerHTML = html
}

// ─── Unified demographic panel ───

const DEMO_SECTIONS = [
  { key: 'race', title: 'Race', labels: { white: 'White', black: 'Black', asian: 'Asian', native_american: 'Native Am.', pacific_islander: 'Pacific Isl.', other: 'Other', two_or_more: 'Two or More' } as Record<string, string> },
  { key: 'ethnicity', title: 'Ethnicity', labels: { hispanic: 'Hispanic', non_hispanic: 'Non-Hispanic' } as Record<string, string> },
  { key: 'income', title: 'Income', labels: { under_25k: 'Under $25k', '25k_50k': '$25k–50k', '50k_100k': '$50k–100k', over_100k: 'Over $100k' } as Record<string, string> },
  { key: 'homeownership', title: 'Homeownership', labels: { owner: 'Owner', renter: 'Renter' } as Record<string, string> },
  { key: 'transportation', title: 'Transportation', labels: { drove_alone: 'Drove Alone', carpool: 'Carpool', transit: 'Transit', walking: 'Walking', bicycle: 'Bicycle', wfh: 'Work from Home' } as Record<string, string> },
  { key: 'age', title: 'Age', labels: { under_18: 'Under 18', '18_24': '18–24', '25_34': '25–34', '35_44': '35–44', '45_54': '45–54', '55_64': '55–64', '65_74': '65–74', '75_84': '75–84', '85_plus': '85+' } as Record<string, string> },
  { key: 'education', title: 'Education', labels: { less_than_hs: 'Less than HS', hs_grad: 'HS Grad/GED', some_college: 'Some College', associates: "Associate's", bachelors: "Bachelor's", masters: "Master's", professional: 'Professional', doctorate: 'Doctorate' } as Record<string, string> },
]

let currentDemoCategory = 0
let currentDemoView: 'total' | 'by_nwi' | 'nwi_by' = 'total'
let currentJurisdiction: Jurisdiction | null = null

function renderDemoPanel(j: Jurisdiction) {
  currentJurisdiction = j
  const container = document.getElementById('demographics')!

  // Category selector + view toggle
  let html = '<div class="demo-controls">'
  html += '<select id="demo-cat-select" class="demo-select">'
  DEMO_SECTIONS.forEach((s, i) => {
    html += `<option value="${i}" ${i === currentDemoCategory ? 'selected' : ''}>${s.title}</option>`
  })
  html += '</select>'
  html += '<div class="demo-view-toggle">'
  html += `<button class="demo-view-btn ${currentDemoView === 'total' ? 'active' : ''}" data-dview="total">Overall</button>`
  html += `<button class="demo-view-btn ${currentDemoView === 'by_nwi' ? 'active' : ''}" data-dview="by_nwi">By WI Level</button>`
  html += `<button class="demo-view-btn ${currentDemoView === 'nwi_by' ? 'active' : ''}" data-dview="nwi_by">WI by Group</button>`
  html += '</div></div>'

  html += '<div id="demo-content"></div>'
  container.innerHTML = html

  // Wire controls
  document.getElementById('demo-cat-select')!.addEventListener('change', (e) => {
    currentDemoCategory = parseInt((e.target as HTMLSelectElement).value)
    renderDemoContent()
  })
  container.querySelectorAll('.demo-view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentDemoView = (btn as HTMLElement).dataset.dview as typeof currentDemoView
      container.querySelectorAll('.demo-view-btn').forEach(b => b.classList.remove('active'))
      btn.classList.add('active')
      renderDemoContent()
    })
  })

  renderDemoContent()
}

function renderDemoContent() {
  const j = currentJurisdiction
  if (!j) return
  const content = document.getElementById('demo-content')!
  const section = DEMO_SECTIONS[currentDemoCategory]

  if (currentDemoView === 'total') {
    renderTotalView(content, j, section)
  } else if (currentDemoView === 'by_nwi') {
    renderByNwiView(content, j, section)
  } else {
    renderNwiByView(content, j, section)
  }
}

function renderTotalView(el: HTMLElement, j: Jurisdiction, section: typeof DEMO_SECTIONS[0]) {
  // Overall composition — "X% White, Y% Black..."
  const totals: Record<string, number> = {}
  for (const [, level] of Object.entries(j.by_nwi)) {
    const cat = level.demographics[section.key]
    if (!cat) continue
    for (const [k, v] of Object.entries(cat)) {
      totals[k] = (totals[k] || 0) + v
    }
  }
  const grandTotal = Object.values(totals).reduce((a, b) => a + b, 0)
  if (grandTotal === 0) { el.innerHTML = '<p class="demo-note">No data available</p>'; return }

  let html = ''
  for (const [key, label] of Object.entries(section.labels)) {
    const val = totals[key] || 0
    const pct = val / grandTotal * 100
    html += `<div class="demo-row">
      <span class="demo-label">${label}</span>
      <div class="demo-bar-bg"><div class="demo-bar-fill" style="width:${pct}%;background:var(--accent)"></div></div>
      <span class="demo-pct">${pct.toFixed(1)}%</span>
    </div>`
  }
  el.innerHTML = html
}

function renderByNwiView(el: HTMLElement, j: Jurisdiction, section: typeof DEMO_SECTIONS[0]) {
  // "Of White residents, X% in Most Walkable, Y% in Least..."
  // Stacked NWI-colored bars per group
  let html = ''
  for (const [key, label] of Object.entries(section.labels)) {
    let total = 0
    const byLevel: number[] = [0, 0, 0, 0]
    for (let i = 0; i < 4; i++) {
      const lvl = j.by_nwi[String(i)]
      if (!lvl) continue
      const val = lvl.demographics[section.key]?.[key] || 0
      byLevel[i] = val
      total += val
    }
    if (total === 0) continue

    html += `<div class="demo-row"><span class="demo-label">${label}</span>`
    html += '<div class="demo-bar-bg"><div class="nwi-stacked-bar">'
    for (let i = 0; i < 4; i++) {
      const pct = byLevel[i] / total * 100
      if (pct > 0) {
        html += `<div class="nwi-stacked-seg" style="width:${pct}%;background:${NWI_COLORS[i]}" title="${NWI_LEVEL_LABELS[i]}: ${pct.toFixed(0)}%"></div>`
      }
    }
    html += '</div></div>'
    const abovePct = (byLevel[2] + byLevel[3]) / total * 100
    html += `<span class="demo-pct">${abovePct.toFixed(0)}%</span></div>`
  }
  html += '<div class="demo-note">Bar: distribution across walkability levels. %: above-average+.</div>'
  el.innerHTML = html
}

function renderNwiByView(el: HTMLElement, j: Jurisdiction, section: typeof DEMO_SECTIONS[0]) {
  // "In Most Walkable areas, X% White, Y% Black..."
  // One stacked bar per NWI level showing composition
  const labels = Object.entries(section.labels)
  // Generate a color palette for the categories
  const catColors = generateCatColors(labels.length)

  let html = ''
  for (let i = 3; i >= 0; i--) {  // Most walkable first
    const lvl = j.by_nwi[String(i)]
    if (!lvl || lvl.population === 0) continue
    const cat = lvl.demographics[section.key]
    if (!cat) continue
    const total = Object.values(cat).reduce((a, b) => a + b, 0)
    if (total === 0) continue

    html += `<div class="demo-row"><span class="demo-label">
      <span class="nwi-dot" style="background:${NWI_COLORS[i]}"></span>${NWI_LEVEL_LABELS[i]}
    </span>`
    html += '<div class="demo-bar-bg"><div class="nwi-stacked-bar">'
    labels.forEach(([key, _label], ci) => {
      const val = cat[key] || 0
      const pct = val / total * 100
      if (pct > 1) {
        html += `<div class="nwi-stacked-seg" style="width:${pct}%;background:${catColors[ci]}" title="${_label}: ${pct.toFixed(0)}%"></div>`
      }
    })
    html += '</div></div>'
    html += '<span class="demo-pct"></span></div>'
  }

  // Legend
  html += '<div class="demo-cat-legend">'
  labels.forEach(([, label], ci) => {
    html += `<span class="demo-cat-legend-item"><span class="nwi-dot" style="background:${catColors[ci]}"></span>${label}</span>`
  })
  html += '</div>'

  el.innerHTML = html
}

function generateCatColors(n: number): string[] {
  // Muted, distinguishable palette
  const palette = [
    '#6b8fa3', '#c4956a', '#7b9e6b', '#b07ba1',
    '#9b8a6e', '#6ba3a0', '#a37070', '#8890b5',
    '#a3a36b', '#6b7ea3', '#a06b8f', '#7ba37b',
  ]
  return palette.slice(0, n)
}

const NWI_LEVEL_LABELS = ['Least Walkable', 'Below Avg', 'Above Avg', 'Most Walkable']

// ─── Methodology panel ───

function showMethodology() {
  const panel = document.getElementById('panel-content')!
  document.getElementById('panel-empty')!.classList.add('hidden')
  panel.classList.remove('hidden')

  document.getElementById('panel-name')!.textContent = 'About This Tool'
  document.getElementById('panel-summary')!.innerHTML = ''
  document.getElementById('nwi-bar')!.innerHTML = ''
  document.getElementById('demographics')!.innerHTML = `
    <div class="demo-section">
      <div class="demo-title">What This Measures</div>
      <p class="method-text">The <strong>National Walkability Index</strong> (EPA, 2021) scores every Census block group in America on how walkable its <em>land use</em> is — intersection density, land use diversity, and transit proximity.</p>
      <p class="method-text">We combined it with <strong>American Community Survey</strong> demographics (2015–2019) to ask: who lives in walkable places, and who doesn't?</p>
    </div>
    <div class="demo-section">
      <div class="demo-title">How to Read the Scores</div>
      <p class="method-text">The walkability index ranges from 1–10. Scores above 6 indicate above-average walkable land use. The four-level classification groups block groups into quartiles.</p>
      <p class="method-text">All metrics are <strong>population-weighted</strong> — a block group with 10,000 residents counts more than one with 10.</p>
    </div>
    <div class="demo-section">
      <div class="demo-title">Known Limitations</div>
      <p class="method-text"><strong>Too dense:</strong> In Manhattan, block groups are so small they can't contain enough intersections or land uses. NYC ranks #83 — below its true walkability.</p>
      <p class="method-text"><strong>Too sparse:</strong> Small walkable towns (like Tekoa, WA) get swallowed by large rural block groups.</p>
      <p class="method-text">The NWI measures <em>land use patterns</em>, not sidewalk quality, pedestrian safety, or actual walking behavior.</p>
    </div>
    <div class="demo-section">
      <div class="demo-title">Data Sources</div>
      <p class="method-text">EPA National Walkability Index (2021 release, 2017–2020 data) · American Community Survey 5-year estimates (2015–2019) · Census 2010 geographies · 220,000 block groups</p>
    </div>
    <div class="demo-section method-credit">
      Built by Elliott Day for <strong>America Walks</strong> · americawalks.org
    </div>
  `
}

// ─── Helpers ───

// zoomToFeature removed — using pre-computed bounds from bounds.json instead

function formatPop(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return Math.round(n / 1_000) + 'k'
  return String(n)
}

// ─── URL routing ───

function updateHash(level?: string, fips?: string) {
  if (!level) { window.location.hash = ''; return }
  window.location.hash = fips ? `${level}/${fips}` : level
}

async function handleHash() {
  const hash = window.location.hash.slice(1) // remove #
  if (!hash) return

  const parts = hash.split('/')
  const level = parts[0] as 'states' | 'counties' | 'cities'
  const fips = parts[1]

  if (level === 'counties' && fips && fips.length === 2) {
    // Drill into a state's counties
    await drillDown(fips)
  } else if (level === 'counties' && fips && fips.length === 5) {
    // Direct link to a county
    await setLevel('counties')
    const data = await loadData('counties')
    const j = data[fips]
    if (j) {
      showPanel(j)
      // Zoom to the state this county is in
      const stateFips = fips.slice(0, 2)
      const bounds = STATE_BOUNDS[stateFips]
      if (bounds) map.fitBounds(bounds, { padding: 40 })
    }
  } else if (['states', 'counties', 'cities'].includes(level)) {
    await setLevel(level)
    if (fips) {
      const data = await loadData(level)
      const j = data[fips]
      if (j) showPanel(j)
    }
  }
}

// ─── Legend ───

function buildLegend() {
  const scale = document.querySelector('.legend-scale')!
  const items = [
    { color: '#3d6b35', label: '7.0+ (Most Walkable)' },
    { color: '#7ebf6e', label: '6.0–7.0' },
    { color: '#c8d868', label: '5.5–6.0' },
    { color: '#e8d030', label: '5.0–5.5' },
    { color: '#e8a030', label: '4.0–5.0' },
    { color: '#e84830', label: '< 4.0 (Least Walkable)' },
  ]
  scale.innerHTML = items.map(i =>
    `<div class="legend-item"><div class="legend-swatch" style="background:${i.color}"></div><span>${i.label}</span></div>`
  ).join('')
}

// ─── View switching ───

let currentView: 'map' | 'table' = 'map'

async function setView(view: 'map' | 'table') {
  currentView = view

  document.querySelectorAll('.view-btn').forEach(btn => {
    btn.classList.toggle('active', (btn as HTMLElement).dataset.view === view)
  })

  const mapContainer = document.getElementById('map-container')!
  const tableContainer = document.getElementById('table-container')!
  const panel = document.getElementById('panel')!
  const legend = document.getElementById('legend')!

  if (view === 'map') {
    mapContainer.classList.remove('hidden')
    tableContainer.classList.add('hidden')
    panel.classList.remove('hidden')
    legend.classList.remove('hidden')
    map.resize()
  } else {
    mapContainer.classList.add('hidden')
    tableContainer.classList.remove('hidden')
    panel.classList.remove('hidden')
    legend.classList.add('hidden')

    const data = await loadData(currentLevel)
    buildTable(tableContainer, data, (fips) => {
      const j = data[fips]
      if (j) showPanel(j)
    })
  }
}

// ─── Init ───

buildLegend()
map = initMap()

// Load pre-computed feature bounds
fetch('/data/bounds.json').then(r => r.json()).then(d => { featureBounds = d })

// Nav button clicks
document.querySelectorAll('.geo-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const level = (btn as HTMLElement).dataset.level as 'states' | 'counties' | 'cities'
    setLevel(level)
    if (currentView === 'table') setView('table') // refresh table
  })
})

// View toggle clicks
document.querySelectorAll('.view-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    setView((btn as HTMLElement).dataset.view as 'map' | 'table')
  })
})

// Info button
document.getElementById('info-btn')!.addEventListener('click', showMethodology)

// Handle initial hash on load
window.addEventListener('hashchange', handleHash)
// Defer hash handling until map is loaded
map.on('load', () => { setTimeout(handleHash, 100) })

// Fix map not filling container on initial load
map.on('load', () => { map.resize() })
window.addEventListener('resize', () => { map.resize() })
// Also resize after a brief delay to catch late layout shifts
setTimeout(() => { map.resize() }, 300)
