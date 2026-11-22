import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Production serves at the site root (http://womtech.io.vn:8010/). Pass
  // VITE_BASE_PATH=/insights/ only for the optional Nginx-prefixed variant.
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react()],
  build: {
    outDir: '../server/static',
    emptyOutDir: true,
  },
})
