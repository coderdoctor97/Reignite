import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Allow the sandboxed preview domain (e.g. *.e2b.app) to reach the
    // dev server, in addition to localhost.
    allowedHosts: ['localhost', '.e2b.app'],
    // Proxy /api requests to the FastAPI backend during development
    proxy: {
      '/api': {
        target: 'http://localhost:8400',
        changeOrigin: true,
      },
    },
  },
})
