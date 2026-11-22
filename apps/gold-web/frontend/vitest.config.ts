import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    environmentOptions: { jsdom: { url: 'http://127.0.0.1:8011/insights/' } },
    globals: true,
    include: ['src/**/*.test.tsx'],
    testTimeout: 30000,
    hookTimeout: 30000,
  },
})
