import path from "path"
import { fileURLToPath } from "url"
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendHttp = env.VITE_BACKEND_URL || "http://127.0.0.1:8000"
  const backendWs = env.VITE_BACKEND_WS_URL || backendHttp.replace(/^http/i, "ws")
  const frontendPort = Number.parseInt(
    env.VITE_DEV_SERVER_PORT || env.FRONTEND_PORT || "5173",
    10
  )

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: Number.isFinite(frontendPort) ? frontendPort : 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: backendHttp,
          changeOrigin: true,
        },
        "/temp": {
          target: backendHttp,
          changeOrigin: true,
        },
        "/ws": {
          target: backendWs,
          ws: true,
        },
      },
    },
  }
})
