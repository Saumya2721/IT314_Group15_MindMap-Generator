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
