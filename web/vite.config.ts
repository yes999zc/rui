import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/health': 'http://127.0.0.1:8080',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Single-page build: 0.8.0 removed the legacy console, so `v2.html` is the only
    // entry. FastAPI serves it at `/v2` (contract C-002 §2) and `/` redirects there.
    rollupOptions: {
      input: {
        v2: 'v2.html',
      },
    },
  },
})
