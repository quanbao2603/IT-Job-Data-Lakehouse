import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/insights/',
  plugins: [react()],
  build: {
    outDir: '../server/static',
    emptyOutDir: true,
  },
})
