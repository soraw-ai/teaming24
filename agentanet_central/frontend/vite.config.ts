import fs from 'node:fs'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

type FrontendRuntimeConfig = {
  host: string
  port: number
  backendUrl: string
}

const DEFAULT_FRONTEND_CONFIG: FrontendRuntimeConfig = {
  host: '0.0.0.0',
  port: 5173,
  backendUrl: 'http://127.0.0.1:8080',
}

const CENTRAL_CONFIG_PATH = path.resolve(__dirname, '../config.yaml')

function stripQuotes(raw: string): string {
  const v = raw.trim()
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1)
  }
  return v
}

function loadFrontendRuntimeConfig(): FrontendRuntimeConfig {
  const resolved: FrontendRuntimeConfig = { ...DEFAULT_FRONTEND_CONFIG }

  // Highest priority: explicit env override.
  if (process.env.AGENTANET_FRONTEND_HOST?.trim()) {
    resolved.host = process.env.AGENTANET_FRONTEND_HOST.trim()
  }
  if (process.env.AGENTANET_FRONTEND_PORT?.trim()) {
    const p = Number(process.env.AGENTANET_FRONTEND_PORT)
    if (Number.isFinite(p) && p > 0) resolved.port = p
  }
  if (process.env.AGENTANET_FRONTEND_BACKEND_URL?.trim()) {
    resolved.backendUrl = process.env.AGENTANET_FRONTEND_BACKEND_URL.trim()
  }

  if (!fs.existsSync(CENTRAL_CONFIG_PATH)) return resolved

  const lines = fs.readFileSync(CENTRAL_CONFIG_PATH, 'utf8').split(/\r?\n/)
  let inFrontendSection = false
  for (const line of lines) {
    if (!line.trim() || line.trim().startsWith('#')) continue

    // Top-level section switch.
    if (/^[a-zA-Z0-9_]+:\s*(#.*)?$/.test(line.trim()) && !line.startsWith(' ')) {
      inFrontendSection = line.trim().startsWith('frontend:')
      continue
    }
    if (!inFrontendSection) continue

    const m = line.match(/^ {2}([a-zA-Z0-9_]+):\s*(.+?)\s*(?:#.*)?$/)
    if (!m) continue
    const key = m[1]
    const value = stripQuotes(m[2])

    if (key === 'host' && value) resolved.host = value
    if (key === 'port') {
      const p = Number(value)
      if (Number.isFinite(p) && p > 0) resolved.port = p
    }
    if (key === 'backend_url' && value) resolved.backendUrl = value
  }

  return resolved
}

const runtimeConfig = loadFrontendRuntimeConfig()

export default defineConfig({
  plugins: [react()],
  server: {
    host: runtimeConfig.host,
    port: runtimeConfig.port,
    proxy: {
      '/api': runtimeConfig.backendUrl,
      '/auth': runtimeConfig.backendUrl,
    },
  },
})
