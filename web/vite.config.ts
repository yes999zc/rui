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
    // Multi-page build: the legacy console keeps `index.html`, v2 is served at
    // `/v2` by FastAPI (contract C-002 §2).
    rollupOptions: {
      input: {
        main: 'index.html',
        v2: 'v2.html',
      },
    },
  },
})
