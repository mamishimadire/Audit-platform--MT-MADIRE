import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 5173 collides with another project on this machine (phone/frontend/web)
    // — pinned to 5180 with strictPort so this never silently drifts onto
    // whichever port happens to be free, which is what caused start-app.ps1
    // to open the wrong app entirely.
    port: 5180,
    strictPort: true,
  },
})
