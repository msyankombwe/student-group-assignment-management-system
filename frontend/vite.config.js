import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const djangoBaseUrl = (env.VITE_DJANGO_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: djangoBaseUrl,
          changeOrigin: true,
        },
      },
    },
  }
})
