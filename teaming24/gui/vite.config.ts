import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import compression from 'vite-plugin-compression'
import net from 'net'

/**
 * Check if a port is available.
 */
async function isPortAvailable(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close()
      resolve(true)
    })
    server.listen(port, 'localhost')
  })
}

/**
 * Find available port with fallback chain.
 * Prioritizes 8088 for dev server since backend typically runs on 3000.
 */
async function findPort(): Promise<number> {
  // Priority order: 8088 (dev) -> 3000 -> random
  // Backend (FastAPI) typically runs on 3000, so dev server prefers 8088
  const preferredPorts = [8088, 3000]
  
  for (const port of preferredPorts) {
    if (await isPortAvailable(port)) {
      return port
    }
    console.log(`Port ${port} is in use, trying next...`)
  }
  
  // All preferred ports occupied, use random available port (0 lets OS choose)
  console.log('All preferred ports occupied, using random port...')
  return 0  // Vite will find an available port
}

export default defineConfig(async ({ mode }) => {
  // Load env file based on mode
  const env = loadEnv(mode, process.cwd(), '')
  
  // ==========================================================================
  // Configuration from environment variables
  // These can override settings from teaming24.yaml for development
  // 
  // Environment variable precedence (matches backend):
  //   TEAMING24_PORT -> server.port in teaming24.yaml
  //   TEAMING24_HOST -> server.host in teaming24.yaml
  // ==========================================================================
  
  // Backend port: use TEAMING24_PORT if set, fallback to VITE_API_TARGET or default 8000
  const backendPort = env.TEAMING24_PORT || '8000'
  const backendHost = env.TEAMING24_HOST || '127.0.0.1'
  // Always use 127.0.0.1 instead of localhost to avoid IPv6 (::1) resolution issues.
  // Uvicorn with host 0.0.0.0 only listens on IPv4, so Node.js resolving
  // localhost → ::1 would cause ECONNREFUSED.
  const resolvedHost = backendHost === '0.0.0.0' || backendHost === 'localhost' ? '127.0.0.1' : backendHost
  const apiTarget = env.VITE_API_TARGET || `http://${resolvedHost}:${backendPort}`
  
  // Frontend dev port: from env or auto-find
  const port = env.VITE_PORT 
    ? parseInt(env.VITE_PORT, 10) 
    : await findPort()
  
  // Resolve display host for logging
  const devHost = env.VITE_HOST || '0.0.0.0'

  // Log configuration for debugging
  console.log(`\n📡 Vite Dev Server Configuration:`)
  console.log(`   Frontend Dev: http://${devHost}:${port}`)
  console.log(`   Backend API:  ${apiTarget}`)
  console.log(``)
  console.log(`   Config source: teaming24/config/teaming24.yaml`)
  console.log(`   Env overrides: TEAMING24_PORT, TEAMING24_HOST, VITE_PORT`)
  console.log(``)
  console.log(`   Start backend: uv run python -m teaming24.server.cli\n`)
  
  return {
    plugins: [
      react(),
      // Gzip compression for production builds
      ...(mode === 'production' ? [compression({ algorithm: 'gzip' })] : []),
    ],
    server: {
      host: env.VITE_HOST || '0.0.0.0',
      port,
      strictPort: port !== 0,  // Only strict if we found a specific port
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          // Add error handling for proxy failures
          configure: (proxy) => {
            proxy.on('error', (err, _req, res) => {
              console.error(`Proxy error: ${err.message}`)
              if (res && !res.headersSent) {
                res.writeHead(503, { 'Content-Type': 'application/json' })
                res.end(JSON.stringify({ 
                  error: 'Backend unavailable', 
                  message: `Cannot connect to ${apiTarget}. Is the backend running?` 
                }))
              }
            })
          },
        },
      },
    },
    preview: {
      port: parseInt(env.VITE_PREVIEW_PORT || '4173', 10),
    },
    build: {
      // Enable source maps for debugging in production
      sourcemap: mode === 'development',
      // Minify CSS
      cssMinify: true,
      // Increase chunk size warning limit (syntax highlighter is large but necessary)
      chunkSizeWarningLimit: 600,
      rollupOptions: {
        output: {
          manualChunks: {
            // React core
            'vendor-react': ['react', 'react-dom'],
            // UI libraries
            'vendor-ui': ['@headlessui/react', '@heroicons/react'],
            // State management
            'vendor-state': ['zustand'],
            // Markdown rendering (large due to syntax highlighter)
            'vendor-markdown': ['react-markdown', 'remark-gfm'],
            // Syntax highlighting (largest dependency - split for lazy loading)
            'vendor-syntax': ['react-syntax-highlighter'],
          },
        },
      },
    },
    // Optimize dependency pre-bundling
    optimizeDeps: {
      include: ['react', 'react-dom', 'zustand', '@headlessui/react', 'clsx'],
    },
  }
})
