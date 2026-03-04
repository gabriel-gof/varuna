import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const configDir = fileURLToPath(new URL('.', import.meta.url))

const readVersionFrom = (filePath) => {
  try {
    const value = readFileSync(filePath, 'utf8').trim()
    return value || null
  } catch {
    return null
  }
}

const appVersion = (
  process.env.APP_VERSION?.trim() ||
  readVersionFrom(resolve(configDir, 'VERSION')) ||
  readVersionFrom(resolve(configDir, '..', 'VERSION'))
)

if (!appVersion) {
  throw new Error(
    'Unable to resolve app version. Expected APP_VERSION env or VERSION file in frontend/ or repo root.'
  )
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  assetsInclude: ['**/*.svg', '**/*.csv'],
  server: {
    host: '0.0.0.0',
    port: 4000,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
