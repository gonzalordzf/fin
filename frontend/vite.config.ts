import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxies /api/* to the FastAPI backend (uvicorn, port 8000 by default) so
// the frontend never needs CORS config or a hardcoded absolute URL.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
