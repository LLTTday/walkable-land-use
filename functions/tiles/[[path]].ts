// Cloudflare Pages Function — proxy PMTiles from GitHub releases with CORS
const BASE = 'https://github.com/LLTTday/walkable-land-use/releases/download/v0.4.0'

export const onRequest: PagesFunction = async ({ params, request }) => {
  const path = (params.path as string[]).join('/')
  const url = `${BASE}/${path}`

  // Forward range headers
  const headers: Record<string, string> = {}
  const range = request.headers.get('Range')
  if (range) headers['Range'] = range

  const resp = await fetch(url, {
    headers,
    redirect: 'follow',
  })

  // Return with CORS headers
  const responseHeaders = new Headers(resp.headers)
  responseHeaders.set('Access-Control-Allow-Origin', '*')
  responseHeaders.set('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
  responseHeaders.set('Access-Control-Allow-Headers', 'Range')
  responseHeaders.set('Access-Control-Expose-Headers', 'Content-Range, Content-Length')

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: responseHeaders })
  }

  return new Response(resp.body, {
    status: resp.status,
    headers: responseHeaders,
  })
}
