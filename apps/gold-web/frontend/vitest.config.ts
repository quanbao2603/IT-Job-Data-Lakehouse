import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// The DOM suite talks to a real instance. Point it anywhere with
// IT_INSIGHT_BASE_URL (defaults to the local deployment on :8010).
const baseUrl = process.env.IT_INSIGHT_BASE_URL ?? 'http://127.0.0.1:8010/'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    environmentOptions: { jsdom: { url: baseUrl } },
    globals: true,
    include: ['src/**/*.test.tsx'],
    testTimeout: 30000,
    hookTimeout: 30000,
  },
})
