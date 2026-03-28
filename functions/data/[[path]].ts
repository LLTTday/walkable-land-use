// Cloudflare Pages Function — proxy large data files from GitHub releases
const BASE = 'https://github.com/LLTTday/walkable-land-use/releases/download/v0.2.0'

const PROXIED = new Set(['cities.json', 'counties.json'])

export const onRequest: PagesFunction = async (context) => {
  const { params, request } = context
  const path = (params.path as string[]).join('/')

  // Large files: proxy from GitHub release
  if (PROXIED.has(path)) {
    const url = `${BASE}/${path}`
    const resp = await fetch(url, { redirect: 'follow' })

    const responseHeaders = new Headers(resp.headers)
    responseHeaders.set('Access-Control-Allow-Origin', '*')
    responseHeaders.set('Content-Type', 'application/json')
    responseHeaders.set('Cache-Control', 'public, max-age=86400')

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: responseHeaders })
    }

    return new Response(resp.body, {
      status: resp.status,
      headers: responseHeaders,
    })
  }

  // Small files: pass through to static assets
  return context.next()
}
