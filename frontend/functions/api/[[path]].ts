// Cloudflare Pages Function: reverse-proxies /api/* to the Fly.io backend.
//
// Why this exists at all: the frontend calls `/api/...` as a same-origin
// path (see src/api.ts and the Vite dev proxy in vite.config.ts). In
// production there's no Vite dev server to do that rewrite, so this
// function plays the same role — same-origin means the session cookie
// (set_by SessionMiddleware, see backend/app/auth.py) stays first-party and
// SameSite=Lax works normally. Proxying through a Worker avoids CORS
// entirely instead of having to configure it on the FastAPI side.
//
// BACKEND_URL is set as a Cloudflare Pages environment variable (project
// settings, not committed here) — e.g. https://finanzas-personales-gonzalo.fly.dev

interface Env {
  BACKEND_URL: string
}

export const onRequest: PagesFunction<Env> = async (context) => {
  const { request, env, params } = context

  if (!env.BACKEND_URL) {
    return new Response('BACKEND_URL is not configured', { status: 500 })
  }

  const path = Array.isArray(params.path) ? params.path.join('/') : (params.path ?? '')
  const incomingUrl = new URL(request.url)
  const targetUrl = `${env.BACKEND_URL.replace(/\/$/, '')}/${path}${incomingUrl.search}`

  const proxied = new Request(targetUrl, {
    method: request.method,
    headers: request.headers,
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
    redirect: 'manual',
  })

  return fetch(proxied)
}
