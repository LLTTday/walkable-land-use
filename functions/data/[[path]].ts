// Cloudflare Pages Function — proxy large data files from GitHub releases
const BASE = 'https://github.com/LLTTday/walkable-land-use/releases/download/v0.2.0'

export const onRequest: PagesFunction = async ({ params, request }) => {
  const path = (params.path as string[]).join('/')

  // Only proxy large files — small ones are served statically from dist
  const PROXIED = ['cities.json', 'counties.json']
  if (!PROXIED.includes(path)) {
    return new Response('Not found', { status: 404 })
  }

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
