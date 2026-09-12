// Next.js configuration.
//
// TWO MODES, ONE APP
// ------------------
// `next build` exports a static site, because the deployment has not changed:
// one FastAPI process serves the API and the built UI from the same origin,
// so the frontend build has to be files on disk. `scripts/publish.mjs` then
// copies them to backend/app/static, which is where vite.config.js used to
// write them directly.
//
// `next dev` instead proxies /api and /health to the backend, which is what
// the Vite dev server's `proxy` block did. Rewrites and a static export are
// mutually exclusive -- an exported site has no server to rewrite anything --
// so each mode declares only the option that applies to it.
const isProduction = process.env.NODE_ENV === 'production'

// Never hard-coded to a host: the default is the port the backend listens on
// locally, and JM_DEV_API_TARGET moves it.
const apiTarget = (process.env.JM_DEV_API_TARGET || 'http://127.0.0.1:8011')
  .replace(/\/$/, '')

/** @type {import('next').NextConfig} */
export default {
  // main.jsx wrapped the app in React.StrictMode. This is the same thing.
  reactStrictMode: true,

  ...(isProduction
    ? { output: 'export' }
    : {
        async rewrites() {
          return [
            { source: '/api/:path*', destination: `${apiTarget}/api/:path*` },
            { source: '/health', destination: `${apiTarget}/health` },
          ]
        },
      }),
}
