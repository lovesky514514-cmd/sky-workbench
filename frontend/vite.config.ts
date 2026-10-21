import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: { main: resolve(import.meta.dirname, 'index.html'), pet: resolve(import.meta.dirname, 'pet.html') },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': process.env.VITE_BACKEND_URL ?? 'http://127.0.0.1:8000',
    },
  },
})
