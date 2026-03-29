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
const NWI_LABELS: readonly string[] = ['Least Walkable', 'Below Average', 'Above Average', 'Most Walkable']
void NWI_LABELS

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
let incorporatedSet: Set<string> | null = null

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
  '56': [[-111.1, 41.0], [-104.1, 45.0]],
}

// ─── Slug infrastructure ───

const FIPS_TO_ABBREV: Record<string, string> = {
  '01': 'al', '02': 'ak', '04': 'az', '05': 'ar', '06': 'ca',
  '08': 'co', '09': 'ct', '10': 'de', '11': 'dc', '12': 'fl',
  '13': 'ga', '15': 'hi', '16': 'id', '17': 'il', '18': 'in',
  '19': 'ia', '20': 'ks', '21': 'ky', '22': 'la', '23': 'me',
  '24': 'md', '25': 'ma', '26': 'mi', '27': 'mn', '28': 'ms',
  '29': 'mo', '30': 'mt', '31': 'ne', '32': 'nv', '33': 'nh',
  '34': 'nj', '35': 'nm', '36': 'ny', '37': 'nc', '38': 'nd',
  '39': 'oh', '40': 'ok', '41': 'or', '42': 'pa', '44': 'ri',
  '45': 'sc', '46': 'sd', '47': 'tn', '48': 'tx', '49': 'ut',
  '50': 'vt', '51': 'va', '53': 'wa', '54': 'wv', '55': 'wi',
  '56': 'wy',
}

// slug → { level, key } lookup, built when data loads
const slugMaps: Record<string, Record<string, string>> = {
  state: {},
  county: {},
  city: {},
}

function toSlug(text: string): string {
  return text.toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

function buildSlugMap(level: 'states' | 'counties' | 'cities', data: DataSet) {
  const singularLevel = level === 'states' ? 'state' : level === 'counties' ? 'county' : 'city'
  const map: Record<string, string> = {}

  for (const [key, j] of Object.entries(data)) {
    let slug: string
    if (level === 'states') {
      slug = toSlug(j.name)
    } else {
      // "Dallas County, Texas" → "dallas-county-tx"
      // "Portland, Oregon" → "portland-or"
      const parts = j.name.split(', ')
      const namePart = toSlug(parts[0])
      const stateFips = level === 'cities' ? key.split('|')[1] : key.slice(0, 2)
      const stateAbbrev = FIPS_TO_ABBREV[stateFips] || ''
      slug = stateAbbrev ? `${namePart}-${stateAbbrev}` : namePart
    }

    // Handle collisions by appending a counter
    if (map[slug]) {
      let i = 2
      while (map[`${slug}-${i}`]) i++
      slug = `${slug}-${i}`
    }
    map[slug] = key
  }

  slugMaps[singularLevel] = map
}

function getSlugForKey(level: 'states' | 'counties' | 'cities', key: string): string {
  const singularLevel = level === 'states' ? 'state' : level === 'counties' ? 'county' : 'city'
  const map = slugMaps[singularLevel]
  for (const [slug, k] of Object.entries(map)) {
    if (k === key) return slug
  }
  return key // fallback to key
}

function resolveSlug(singularLevel: string, slugOrFips: string): { level: 'states' | 'counties' | 'cities'; key: string } | null {
  const levelMap: Record<string, 'states' | 'counties' | 'cities'> = {
    state: 'states', county: 'counties', city: 'cities',
  }
  const dataLevel = levelMap[singularLevel]
  if (!dataLevel) return null

  const map = slugMaps[singularLevel]
  // Try slug first
  if (map[slugOrFips]) return { level: dataLevel, key: map[slugOrFips] }
  // Try as FIPS/key directly
  const data = dataCache[dataLevel]
  if (data && data[slugOrFips]) return { level: dataLevel, key: slugOrFips }

  return null
}

// ─── Data loading ───

async function loadData(level: string): Promise<DataSet> {
  if (dataCache[level]) return dataCache[level]
  const resp = await fetch(`/data/${level}.json`)
  const data: DataSet = await resp.json()
  dataCache[level] = data
  if (['states', 'counties', 'cities'].includes(level)) {
    buildSlugMap(level as 'states' | 'counties' | 'cities', data)
  }
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
        paint: { 'background-color': '#5a5755' },
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
      promoteId: { states: 'FIPS', counties: 'FIPS', places: 'FIPS', places_points: 'FIPS' },
    })

    // Base land fill — always visible, light gray underneath everything
    m.addLayer({
      id: 'land',
      type: 'fill',
      source: 'boundaries',
      'source-layer': 'states',
      paint: { 'fill-color': '#f0efed', 'fill-opacity': 1 },
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

    // State outline — thickens at high zoom so it reads above BG fills
    m.addLayer({
      id: 'states-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'states',
      paint: {
        'line-color': ['interpolate', ['linear'], ['zoom'], 6, '#ffffff', 8, '#4a4540'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 3, 1, 8, 2.5],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.8, 8, 0.9],
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

    // County outline — thickens at high zoom
    m.addLayer({
      id: 'counties-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'counties',
      paint: {
        'line-color': ['interpolate', ['linear'], ['zoom'], 6, '#ffffff', 8, '#6b6560'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.5, 8, 1.5],
        'line-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.6, 8, 0.8],
      },
      layout: { visibility: 'none' },
    })

    // ─── City dots (low zoom): progressive disclosure by population ───
    // Visible at low zooms where polygons are too small to see,
    // tippecanoe:minzoom controls when each dot appears in tiles
    m.addLayer({
      id: 'places-dots',
      type: 'circle',
      source: 'boundaries',
      'source-layer': 'places_points',
      maxzoom: 7,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'],
          3, 2,
          5, 3,
          7, 5,
        ],
        'circle-color': [
          'step', ['get', 'nwi'],
          '#e8e4e0',
          0.1, '#e84830',
          4.0, '#e8a030',
          5.0, '#e8d030',
          5.5, '#c8d868',
          6.0, '#7ebf6e',
          7.0, '#5a9a4a',
          8.0, '#3d6b35',
        ],
        'circle-opacity': ['interpolate', ['linear'], ['zoom'],
          5, 0.9,
          7, 0,
        ],
        'circle-stroke-width': 0.5,
        'circle-stroke-color': 'rgba(255,255,255,0.6)',
        'circle-stroke-opacity': ['interpolate', ['linear'], ['zoom'],
          5, 1,
          7, 0,
        ],
      },
      layout: { visibility: 'none' },
    })

    // ─── City polygon fills: colored by embedded nwi ───
    // Visible from z4 all the way through, fading out as BGs take over
    m.addLayer({
      id: 'places-fill',
      type: 'fill',
      source: 'boundaries',
      'source-layer': 'places',
      paint: {
        'fill-color': [
          'step', ['get', 'nwi'],
          '#e8e4e0',
          0.1, '#e84830',
          4.0, '#e8a030',
          5.0, '#e8d030',
          5.5, '#c8d868',
          6.0, '#7ebf6e',
          7.0, '#5a9a4a',
          8.0, '#3d6b35',
        ],
        'fill-opacity': ['interpolate', ['linear'], ['zoom'],
          3, 0.85,
          9, 0.85,
          10, 0,
        ],
      },
      layout: { visibility: 'none' },
    })

    // ─── City boundary outlines (z9+): thin lines over BG fills ───
    m.addLayer({
      id: 'places-line',
      type: 'line',
      source: 'boundaries',
      'source-layer': 'places',
      minzoom: 9,
      paint: {
        'line-color': '#4a4540',
        'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.5, 12, 1.5],
        'line-opacity': ['interpolate', ['linear'], ['zoom'],
          9, 0,
          10, 0.7,
        ],
      },
      layout: { visibility: 'none' },
    })

    // Block group layer (below dim — visible through the cutout hole)
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
      minzoom: 9,
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
        'fill-opacity': [
          'interpolate', ['linear'], ['zoom'],
          9, 0,
          10, 0.85,
        ],
      },
    })

    // ─── Selection dim with cutout (GeoJSON source — above BGs) ───
    // A world-covering polygon with the selected feature as an interior hole.
    // Everything outside the hole is dimmed; BGs inside are visible.
    m.addSource('dim-mask', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    })
    m.addLayer({
      id: 'dim-overlay',
      type: 'fill',
      source: 'dim-mask',
      paint: { 'fill-color': '#000000', 'fill-opacity': 0.45 },
    })

    // No BG boundary lines — fill-only choropleth is cleaner

    // ─── Hover highlight layers (feature-state driven) ───
    for (const sl of ['states', 'counties', 'places'] as const) {
      m.addLayer({
        id: `highlight-${sl}`,
        type: 'line',
        source: 'boundaries',
        'source-layer': sl,
        paint: {
          'line-color': '#1a1a1a',
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 2.5, 0],
        },
      })
      m.addLayer({
        id: `highlight-inner-${sl}`,
        type: 'line',
        source: 'boundaries',
        'source-layer': sl,
        paint: {
          'line-color': '#ffffff',
          'line-width': ['case', ['boolean', ['feature-state', 'hover'], false], 1, 0],
        },
      })
    }

    // ─── Selection outline layers (filter-driven) ───
    for (const sl of ['states', 'counties', 'places'] as const) {
      m.addLayer({
        id: `selected-${sl}`,
        type: 'line',
        source: 'boundaries',
        'source-layer': sl,
        filter: ['==', ['get', 'FIPS'], ''],
        paint: { 'line-color': '#1a1a1a', 'line-width': 4 },
      })
      m.addLayer({
        id: `selected-inner-${sl}`,
        type: 'line',
        source: 'boundaries',
        'source-layer': sl,
        filter: ['==', ['get', 'FIPS'], ''],
        paint: { 'line-color': '#ffffff', 'line-width': 2 },
      })
    }

    // ─── Labels ───

    // City labels — single layer, continuous size by population.
    // log10(pop) maps ~3.7 (5k) to ~6.9 (8M) — scaled to font sizes.
    // Bold for 500k+, regular for the rest.
    // Tippecanoe minzoom controls when features enter tiles;
    // collision detection + symbol-sort-key handles density.
    m.addLayer({
      id: 'city-labels',
      type: 'symbol',
      source: 'boundaries',
      'source-layer': 'places_points',
      minzoom: 3,
      filter: ['>=', ['get', 'pop'],
        ['step', ['zoom'], 500000, 5, 100000, 7, 25000, 8, 0],
      ] as unknown as maplibregl.FilterSpecification,
      layout: {
        'text-field': ['get', 'NAME'],
        'text-font': ['case', ['>=', ['get', 'pop'], 500000],
          ['literal', ['Noto Sans Bold']], ['literal', ['Noto Sans Regular']]],
        'text-size': ['interpolate', ['linear'], ['zoom'],
          // At low zoom: spread 8–16px across pop range
          3, ['interpolate', ['linear'], ['ln', ['max', ['get', 'pop'], 1]],
            Math.log(5000), 8, Math.log(1000000), 16],
          // At high zoom: spread 10–20px
          10, ['interpolate', ['linear'], ['ln', ['max', ['get', 'pop'], 1]],
            Math.log(5000), 10, Math.log(1000000), 20],
        ],
        'text-allow-overlap': false,
        'text-optional': true,
        'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
        'text-radial-offset': 0.5,
        'symbol-sort-key': ['-', ['get', 'pop']],
        'visibility': 'none',
      },
      paint: {
        'text-color': '#333333',
        'text-halo-color': 'rgba(255,255,255,0.8)',
        'text-halo-width': 1.5,
        'text-halo-blur': 0.5,
        'text-opacity': ['interpolate', ['linear'], ['zoom'], 10, 1, 12, 0.4],
      },
    })

    // County labels — centroids from PMTiles, hidden by default
    m.addLayer({
      id: 'county-labels',
      type: 'symbol',
      source: 'boundaries',
      'source-layer': 'counties_points',
      minzoom: 6,
      layout: {
        'text-field': ['get', 'NAME'],
        'text-font': ['Noto Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'], 3, 10, 7, 12, 10, 14],
        'text-allow-overlap': false,
        'text-variable-anchor': ['center', 'top', 'bottom', 'left', 'right'],
        'text-padding': 2,
        'visibility': 'none',
      },
      paint: {
        'text-color': '#555555',
        'text-halo-color': 'rgba(255,255,255,0.7)',
        'text-halo-width': 1.5,
        'text-halo-blur': 0.5,
        'text-opacity': ['interpolate', ['linear'], ['zoom'], 10, 1, 12, 0.4],
      },
    })

    // Load initial data, color the map, show national stats
    setLevel('states')
    fetch('/data/national.json').then(r => r.json()).then(j => showPanel(j))

    // ─── Click handler (single, idle-gated) ───
    let isAnimating = false

    m.on('click', (e) => {
      if (isAnimating) return

      // Query the active layer — for cities, try both dots and polygons
      let features: maplibregl.MapGeoJSONFeature[]
      if (currentLevel === 'cities') {
        features = m.queryRenderedFeatures(e.point, { layers: ['places-dots', 'places-fill'] })
      } else {
        const activeLayer = currentLevel === 'states' ? 'states-fill' : 'counties-fill'
        features = m.queryRenderedFeatures(e.point, { layers: [activeLayer] })
      }
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
      'places-dots': 'places_points',
    }

    for (const fillLayer of ['states-fill', 'counties-fill', 'places-fill', 'places-dots']) {
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

    // ─── Block group hover tooltip (zoom 7+) ───
    let bgTooltip: maplibregl.Popup | null = null

    m.on('mousemove', 'bg-fill', (e) => {
      if (!e.features?.length) return
      const props = e.features[0].properties!
      const score = props.s as number

      const levelLabel = score >= 6 ? 'Most Walkable'
        : score >= 5 ? 'Above Average'
        : score >= 4 ? 'Below Average'
        : 'Least Walkable'
      const levelIdx = score >= 6 ? 3 : score >= 5 ? 2 : score >= 4 ? 1 : 0

      const html = `<div class="bg-popup"><div class="bg-popup-score" style="border-left:3px solid ${NWI_COLORS[levelIdx]}"><strong>${score.toFixed(1)}</strong> <span class="bg-popup-label">${levelLabel}</span></div></div>`

      if (!bgTooltip) {
        bgTooltip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, maxWidth: '180px', offset: 12, className: 'bg-tooltip' })
          .addTo(m)
      }
      bgTooltip.setLngLat(e.lngLat).setHTML(html)
    })

    m.on('mouseleave', 'bg-fill', () => {
      if (bgTooltip) { bgTooltip.remove(); bgTooltip = null }
    })

    // ─── City/county hover tooltip ───
    let featureTooltip: maplibregl.Popup | null = null

    for (const layerId of ['places-fill', 'places-dots', 'counties-fill']) {
      m.on('mousemove', layerId, (e) => {
        if (!e.features?.length) return
        const props = e.features[0].properties!
        const fips = props.FIPS as string

        // Look up name + score from loaded data
        const level = layerId.startsWith('places') || layerId === 'places-dots' ? 'cities' : 'counties'
        const data = dataCache[level]
        const j = data?.[fips]

        // Fall back to tile properties if data not loaded yet
        const name = j?.name || props.NAME as string
        const nwi = j?.avg_nwi ?? (props.nwi as number)
        if (!name) return

        const levelLabel = nwi >= 6 ? 'Most Walkable'
          : nwi >= 5 ? 'Above Average'
          : nwi >= 4 ? 'Below Average'
          : 'Least Walkable'
        const levelIdx = nwi >= 6 ? 3 : nwi >= 5 ? 2 : nwi >= 4 ? 1 : 0

        const html = `<div class="bg-popup"><strong>${name}</strong><div class="bg-popup-score" style="border-left:3px solid ${NWI_COLORS[levelIdx]}"><strong>${nwi.toFixed(1)}</strong> <span class="bg-popup-label">${levelLabel}</span></div></div>`

        if (!featureTooltip) {
          featureTooltip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, maxWidth: '240px', offset: 12, className: 'bg-tooltip' })
            .addTo(m)
        }
        featureTooltip.setLngLat(e.lngLat).setHTML(html)
      })

      m.on('mouseleave', layerId, () => {
        if (featureTooltip) { featureTooltip.remove(); featureTooltip = null }
      })
    }
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

  // Hide all fill/line/dot/label layers
  for (const id of ['states-fill', 'states-line', 'counties-fill', 'counties-line',
                     'places-fill', 'places-line', 'places-dots',
                     'city-labels',
                     'county-labels']) {
    map.setLayoutProperty(id, 'visibility', 'none')
  }

  // Show layers for the active level
  if (level === 'states') {
    map.setLayoutProperty('states-fill', 'visibility', 'visible')
    map.setLayoutProperty('states-line', 'visibility', 'visible')
    colorMap(data, 'states-fill', 'states')
  } else if (level === 'counties') {
    map.setLayoutProperty('counties-fill', 'visibility', 'visible')
    map.setLayoutProperty('counties-line', 'visibility', 'visible')
    map.setLayoutProperty('states-line', 'visibility', 'visible')
    map.setLayoutProperty('county-labels', 'visibility', 'visible')
    colorMap(data, 'counties-fill', 'counties')
  } else {
    // Cities: dots + fills + outlines all visible (zoom controls which shows)
    map.setLayoutProperty('places-dots', 'visibility', 'visible')
    map.setLayoutProperty('places-fill', 'visibility', 'visible')
    map.setLayoutProperty('places-line', 'visibility', 'visible')
    map.setLayoutProperty('states-line', 'visibility', 'visible')
    map.setLayoutProperty('city-labels', 'visibility', 'visible')
    // No colorMap needed — places use embedded nwi property in tile data
  }

  // Clear selection highlight and hide inactive highlight layers
  clearSelection()
  const slMap: Record<string, string> = { states: 'states', counties: 'counties', cities: 'places' }
  for (const [lvl, sl] of Object.entries(slMap)) {
    const vis = (lvl === level || lvl === 'states') ? 'visible' : 'none'
    map.setLayoutProperty(`highlight-${sl}`, 'visibility', vis)
    map.setLayoutProperty(`highlight-inner-${sl}`, 'visibility', vis)
    map.setLayoutProperty(`selected-${sl}`, 'visibility', vis)
    map.setLayoutProperty(`selected-inner-${sl}`, 'visibility', vis)
  }

  // Reset panel
  showEmptyPanel()
}

// ─── Choropleth coloring ───

function colorMap(data: DataSet, layerId: string, _sourceLayer: string) {
  // States and counties use a FIPS→score match expression (small enough).
  // Places use embedded 'nwi' property in tile data — no colorMap call needed.
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
    const sl = selectedFeature.sourceLayer
    // Clear dim mask
    ;(map.getSource('dim-mask') as maplibregl.GeoJSONSource).setData({ type: 'FeatureCollection', features: [] })
    // Remove outline
    map.setFilter(`selected-${sl}`, ['==', ['get', 'FIPS'], ''])
    map.setFilter(`selected-inner-${sl}`, ['==', ['get', 'FIPS'], ''])
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
  map.setLayoutProperty('places-dots', 'visibility', 'none')

  // Color counties
  colorMap(countyData, 'counties-fill', 'counties')

  // Highlight drilled state
  clearSelection()
  map.setFilter('selected-states', ['==', ['get', 'FIPS'], stateFips])
  map.setFilter('selected-inner-states', ['==', ['get', 'FIPS'], stateFips])
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

  // Highlight via filter (robust — doesn't depend on tile-loaded feature-state)
  clearSelection()
  const sourceLayer = level === 'cities' ? 'places' : 'counties'

  // Build dim mask: world polygon with selected feature as a hole
  const fillLayer = sourceLayer === 'places' ? 'places-fill' : `${sourceLayer}-fill`
  const rendered = map.queryRenderedFeatures(undefined as any, { layers: [fillLayer], filter: ['==', ['get', 'FIPS'], fips] })
  if (rendered.length > 0) {
    const geom = rendered[0].geometry
    // World exterior ring (covers entire viewport and beyond)
    const world = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]
    // Extract hole rings from the selected feature
    let holes: number[][][] = []
    if (geom.type === 'Polygon') {
      holes = geom.coordinates.map(ring => [...ring].reverse())
    } else if (geom.type === 'MultiPolygon') {
      // Use the largest polygon as the hole
      for (const poly of geom.coordinates) {
        holes.push(...poly.map(ring => [...ring].reverse()))
      }
    }
    const mask: GeoJSON.Feature = {
      type: 'Feature',
      properties: {},
      geometry: { type: 'Polygon', coordinates: [world, ...holes] },
    }
    ;(map.getSource('dim-mask') as maplibregl.GeoJSONSource).setData(mask)
  }
  // Outline the selected feature
  map.setFilter(`selected-${sourceLayer}`, ['==', ['get', 'FIPS'], fips])
  map.setFilter(`selected-inner-${sourceLayer}`, ['==', ['get', 'FIPS'], fips])
  selectedFeature = { id: fips, sourceLayer }

  // Zoom to feature
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

  const nameEl = document.getElementById('panel-name')!
  nameEl.textContent = j.name

  // Add "Full View →" link (only for real jurisdictions, not national)
  const existingLink = nameEl.querySelector('.jv-link')
  if (existingLink) existingLink.remove()
  const slug = findJurisdictionSlug(j)
  if (slug) {
    const link = document.createElement('a')
    link.className = 'jv-link'
    link.textContent = ' Full View →'
    link.href = `#${slug}`
    link.addEventListener('click', (e) => {
      e.preventDefault()
      window.location.hash = slug
    })
    nameEl.appendChild(link)
  }

  // Summary stats
  document.getElementById('panel-summary')!.innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${formatPop(j.population)}</div>
      <div class="stat-label">Population</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${j.avg_nwi.toFixed(1)}</div>
      <div class="stat-label">Avg Walkability</div>
    </div>
  `

  // NWI bar
  renderNwiBar(j)

  // Demographics — with view toggle
  renderDemoPanel(j)
}

function buildNwiBarHtml(j: Jurisdiction): string {
  const total = j.population
  if (total === 0) return ''

  let html = '<div class="nwi-bar-container">'
  for (let i = 0; i < 4; i++) {
    const pop = j.by_nwi[String(i)]?.population || 0
    const pct = pop / total * 100
    if (pct > 0) {
      const tooltip = `${NWI_LEVEL_LABELS[i]}: ${Math.round(pct)}% (${pop.toLocaleString()})`
      html += `<div class="nwi-bar-segment" data-level="${i}" data-tooltip="${tooltip}" style="width:${pct}%;background:${NWI_COLORS[i]}"></div>`
    }
  }
  html += '</div>'
  html += '<div class="nwi-bar-legend">'
  for (let i = 0; i < 4; i++) {
    html += `<span class="nwi-bar-legend-item"><span class="nwi-dot" style="background:${NWI_COLORS[i]}"></span>${NWI_LEVEL_LABELS[i]}</span>`
  }
  html += '</div>'
  return html
}

function renderNwiBar(j: Jurisdiction) {
  document.getElementById('nwi-bar')!.innerHTML = buildNwiBarHtml(j)
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
let currentDemoView: 'total' | 'by_nwi' | 'nwi_by' = 'by_nwi'
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

const DEMO_MIN_POP = 500

function renderDemoContent() {
  const j = currentJurisdiction
  if (!j) return
  const content = document.getElementById('demo-content')!
  const section = DEMO_SECTIONS[currentDemoCategory]

  if (j.population < DEMO_MIN_POP) {
    content.innerHTML = '<p class="demo-note">Demographics not shown for jurisdictions under 500 population — area-weighted estimates are unreliable at this scale.</p>'
    return
  }

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
        const tooltip = `${NWI_LEVEL_LABELS[i]}: ${Math.round(pct)}% (${byLevel[i].toLocaleString()})`
        html += `<div class="nwi-stacked-seg" data-tooltip="${tooltip}" style="width:${pct}%;background:${NWI_COLORS[i]}"></div>`
      }
    }
    html += '</div></div></div>'
  }
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
        const tooltip = `${_label}: ${Math.round(pct)}% (${val.toLocaleString()})`
        html += `<div class="nwi-stacked-seg" data-tooltip="${tooltip}" style="width:${pct}%;background:${catColors[ci]}"></div>`
      }
    })
    html += '</div></div></div>'
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

// ─── Jurisdiction view ───

function findJurisdictionSlug(j: Jurisdiction): string | null {
  // Search all loaded datasets for this jurisdiction
  for (const level of ['states', 'counties', 'cities'] as const) {
    const data = dataCache[level]
    if (!data) continue
    for (const [key, val] of Object.entries(data)) {
      if (val === j) {
        const singularLevel = level === 'states' ? 'state' : level === 'counties' ? 'county' : 'city'
        return `${singularLevel}/${getSlugForKey(level, key)}`
      }
    }
  }
  return null
}

let jvCurrentView: 'total' | 'by_nwi' | 'nwi_by' = 'by_nwi'

function showJurisdictionView(level: 'states' | 'counties' | 'cities', key: string, j: Jurisdiction) {
  const jv = document.getElementById('jurisdiction-view')!
  const header = document.getElementById('header')!
  const main = document.getElementById('main')!

  header.classList.add('hidden')
  main.classList.add('hidden')
  jv.classList.remove('hidden')

  jvCurrentView = 'by_nwi' // reset to default

  const nwiBarHtml = buildNwiBarHtml(j)

  jv.innerHTML = `
    <div class="jv-header">
      <button class="jv-back" id="jv-back-btn">← Back to Map</button>
      <a href="https://americawalks.org" id="jv-logo" target="_blank" rel="noopener">
        <img src="/aw-logo.png" alt="America Walks" style="height:28px" />
      </a>
    </div>
    <div class="jv-body">
      <h1 class="jv-name">${j.name}</h1>
      <div class="jv-stats">
        <div class="stat-card">
          <div class="stat-value">${formatPop(j.population)}</div>
          <div class="stat-label">Population</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">${j.avg_nwi.toFixed(1)}</div>
          <div class="stat-label">Avg Walkability</div>
        </div>
      </div>
      <div class="jv-nwi-bar">${nwiBarHtml}</div>
      <div class="jv-demo-controls">
        <div class="demo-view-toggle">
          <button class="demo-view-btn" data-jvview="total">Overall</button>
          <button class="demo-view-btn active" data-jvview="by_nwi">By WI Level</button>
          <button class="demo-view-btn" data-jvview="nwi_by">WI by Group</button>
        </div>
      </div>
      <div class="jv-demo-grid" id="jv-demo-grid"></div>
    </div>
  `

  // Wire back button
  document.getElementById('jv-back-btn')!.addEventListener('click', () => {
    hideJurisdictionView()
    // Navigate to explorer with this jurisdiction's level active
    setLevel(level)
    const data = dataCache[level]
    if (data && data[key]) showPanel(data[key])
    updateHash(level, key)
  })

  // Wire view toggle
  jv.querySelectorAll('[data-jvview]').forEach(btn => {
    btn.addEventListener('click', () => {
      jvCurrentView = (btn as HTMLElement).dataset.jvview as typeof jvCurrentView
      jv.querySelectorAll('[data-jvview]').forEach(b => b.classList.remove('active'))
      btn.classList.add('active')
      renderJvDemoGrid(j)
    })
  })

  renderJvDemoGrid(j)
}

function renderJvDemoGrid(j: Jurisdiction) {
  const grid = document.getElementById('jv-demo-grid')!

  if (j.population < DEMO_MIN_POP) {
    grid.innerHTML = '<p class="demo-note" style="grid-column:1/-1">Demographics not shown for jurisdictions under 500 population — area-weighted estimates are unreliable at this scale.</p>'
    return
  }

  let html = ''

  for (const section of DEMO_SECTIONS) {
    html += `<div class="jv-demo-card">`
    html += `<div class="demo-title">${section.title}</div>`
    html += `<div class="jv-demo-content" data-section="${section.key}"></div>`
    html += `</div>`
  }

  grid.innerHTML = html

  // Render each section
  grid.querySelectorAll('.jv-demo-content').forEach(el => {
    const key = (el as HTMLElement).dataset.section!
    const section = DEMO_SECTIONS.find(s => s.key === key)!
    if (jvCurrentView === 'total') {
      renderTotalView(el as HTMLElement, j, section)
    } else if (jvCurrentView === 'by_nwi') {
      renderByNwiView(el as HTMLElement, j, section)
    } else {
      renderNwiByView(el as HTMLElement, j, section)
    }
  })
}

function hideJurisdictionView() {
  const jv = document.getElementById('jurisdiction-view')!
  const header = document.getElementById('header')!
  const main = document.getElementById('main')!

  jv.classList.add('hidden')
  header.classList.remove('hidden')
  main.classList.remove('hidden')

  // Resize map since it was hidden
  if (map) {
    map.resize()
  }
}

// ─── URL routing ───

let _hashFromCode = false  // suppress handleHash when we set the hash programmatically

function updateHash(level?: string, fips?: string) {
  _hashFromCode = true
  if (!level) { window.location.hash = ''; return }
  window.location.hash = fips ? `${level}/${fips}` : level
}

async function handleHash() {
  if (_hashFromCode) { _hashFromCode = false; return }

  const hash = window.location.hash.slice(1) // remove #
  if (!hash) {
    hideJurisdictionView()
    return
  }

  const parts = hash.split('/')
  const level = parts[0]
  const slugOrFips = parts[1]

  // Jurisdiction view routes (singular: state/, county/, city/)
  if (['state', 'county', 'city'].includes(level) && slugOrFips) {
    const pluralLevel = level === 'state' ? 'states' : level === 'county' ? 'counties' : 'cities'
    await loadData(pluralLevel) // ensure slug map is built
    const resolved = resolveSlug(level, slugOrFips)
    if (resolved) {
      const data = await loadData(resolved.level)
      const j = data[resolved.key]
      if (j) {
        showJurisdictionView(resolved.level, resolved.key, j)
        return
      }
    }
  }

  // Explorer routes (plural: states, counties/, cities)
  hideJurisdictionView()
  const explorerLevel = level as 'states' | 'counties' | 'cities'

  if (explorerLevel === 'counties' && slugOrFips && slugOrFips.length === 2) {
    // Drill into a state's counties
    await drillDown(slugOrFips)
  } else if (explorerLevel === 'counties' && slugOrFips && slugOrFips.length === 5) {
    // Direct link to a county
    await setLevel('counties')
    const data = await loadData('counties')
    const j = data[slugOrFips]
    if (j) {
      showPanel(j)
      // Zoom to the state this county is in
      const stateFips = slugOrFips.slice(0, 2)
      const bounds = STATE_BOUNDS[stateFips]
      if (bounds) map.fitBounds(bounds, { padding: 40 })
    }
  } else if (['states', 'counties', 'cities'].includes(explorerLevel)) {
    await setLevel(explorerLevel)
    if (slugOrFips) {
      const data = await loadData(explorerLevel)
      const j = data[slugOrFips]
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
    }, { level: currentLevel, incorporatedSet: incorporatedSet || undefined })
  }
}

// ─── Bar tooltips ───

function initBarTooltips() {
  const tip = document.createElement('div')
  tip.className = 'bar-tooltip'
  document.body.appendChild(tip)

  document.addEventListener('mouseover', (e) => {
    const seg = (e.target as HTMLElement).closest('[data-tooltip]') as HTMLElement | null
    if (!seg) return
    tip.textContent = seg.dataset.tooltip!
    tip.classList.add('visible')
    const rect = seg.getBoundingClientRect()
    tip.style.left = `${rect.left + rect.width / 2 - tip.offsetWidth / 2}px`
    tip.style.top = `${rect.top - tip.offsetHeight - 6}px`
  })

  document.addEventListener('mouseout', (e) => {
    const seg = (e.target as HTMLElement).closest('[data-tooltip]')
    if (seg) tip.classList.remove('visible')
  })
}

// ─── Init ───

buildLegend()
initBarTooltips()
map = initMap()

// Load pre-computed feature bounds
fetch('/data/bounds.json').then(r => r.json()).then(d => { featureBounds = d })
fetch('/data/incorporated.json').then(r => r.json()).then((d: string[]) => { incorporatedSet = new Set(d) })

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

// Convert path-based URLs to hash routes (for share card links)
// /city/portland-or → #city/portland-or
const _path = window.location.pathname
if (/^\/(state|county|city)\//.test(_path)) {
  window.location.hash = _path.slice(1)
  history.replaceState(null, '', '/' + window.location.hash)
}

// Handle initial hash on load
window.addEventListener('hashchange', handleHash)
// Defer hash handling until map is loaded
map.on('load', () => { setTimeout(handleHash, 100) })

// Fix map not filling container on initial load
map.on('load', () => { map.resize() })
window.addEventListener('resize', () => { map.resize() })
// Also resize after a brief delay to catch late layout shifts
setTimeout(() => { map.resize() }, 300)
