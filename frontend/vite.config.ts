import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In Docker dev, the backend is reachable at http://backend:8000.
// Locally (host), it is at http://localhost:8000.
const backendTarget = process.env.VITE_API_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
