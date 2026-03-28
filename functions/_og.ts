// Shared OG tag logic for jurisdiction Pages Functions

const BASE_URL = 'https://walkable-land-use.pages.dev'

const CRAWLER_UA = /Twitterbot|facebookexternalhit|Slackbot|LinkedInBot|Discordbot|WhatsApp|Googlebot|bingbot|Embedly|Iframely|Pinterestbot/i

interface JurisdictionMeta {
  name: string
  score: number
  pop: number
  above: number
}

function formatPop(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return Math.round(n / 1_000) + 'k'
  return String(n)
}

function buildOgHtml(
  level: string,
  slug: string,
  meta: JurisdictionMeta,
): string {
  const url = `${BASE_URL}/${level}/${slug}`
  const title = `${meta.name} — Walkability Index | America Walks`
  const description = `Walkability score: ${meta.score.toFixed(1)}/10. ${formatPop(meta.pop)} population. ${meta.above}% live in above-average walkable areas.`
  const image = `${BASE_URL}/og-image.png`

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>${title}</title>
<meta name="description" content="${description}">
<meta property="og:title" content="${title}">
<meta property="og:description" content="${description}">
<meta property="og:image" content="${image}">
<meta property="og:url" content="${url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Walkable Land Use — America Walks">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${title}">
<meta name="twitter:description" content="${description}">
<meta name="twitter:image" content="${image}">
<link rel="canonical" href="${url}">
<meta http-equiv="refresh" content="0;url=${BASE_URL}/#${level}/${slug}">
</head>
<body>
<p>Redirecting to <a href="${BASE_URL}/#${level}/${slug}">${meta.name}</a>...</p>
</body>
</html>`
}

// Cache the OG index in module scope (persists across requests within same isolate)
let ogIndex: Record<string, JurisdictionMeta> | null = null

async function loadIndex(origin: string): Promise<Record<string, JurisdictionMeta>> {
  if (ogIndex) return ogIndex
  const resp = await fetch(`${origin}/data/og-index.json`)
  ogIndex = await resp.json() as Record<string, JurisdictionMeta>
  return ogIndex
}

export async function handleOgRequest(
  context: { request: Request; params: { slug: string | string[] }; next: () => Promise<Response> },
  level: string,
): Promise<Response> {
  const { request, params } = context
  const slug = Array.isArray(params.slug) ? params.slug.join('/') : params.slug
  const ua = request.headers.get('User-Agent') || ''

  const origin = new URL(request.url).origin

  // Browsers get the SPA — it reads the path and converts to hash route
  if (!CRAWLER_UA.test(ua)) {
    const spaResp = await fetch(`${origin}/index.html`)
    return new Response(spaResp.body, {
      status: 200,
      headers: { 'Content-Type': 'text/html;charset=UTF-8' },
    })
  }

  // Crawlers get OG meta tags
  const index = await loadIndex(origin)
  const key = `${level}/${slug}`
  const meta = index[key]

  if (!meta) {
    // Unknown jurisdiction — serve SPA as fallback
    const spaResp = await fetch(`${origin}/index.html`)
    return new Response(spaResp.body, {
      status: 200,
      headers: { 'Content-Type': 'text/html;charset=UTF-8' },
    })
  }

  const html = buildOgHtml(level, slug, meta)
  return new Response(html, {
    status: 200,
    headers: {
      'Content-Type': 'text/html;charset=UTF-8',
      'Cache-Control': 'public, max-age=86400',
    },
  })
}
