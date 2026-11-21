import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    open: false,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        bypass(req) {
          // Serve index.html for SPA page navigations like /auth/callback
          if (
            req.url?.startsWith('/auth/callback') ||
            req.headers?.accept?.includes('text/html')
          ) {
            return '/index.html';
          }
        },
      },
      '/me': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/internal': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
