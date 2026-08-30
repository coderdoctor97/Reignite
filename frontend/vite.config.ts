import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy /api requests to the FastAPI backend during development
    proxy: {
      '/api': {
        target: 'http://localhost:8400',
        changeOrigin: true,
      },
    },
  },
})
