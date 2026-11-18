import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';

// Production builds only (the dev server injects inline scripts). Blocks injected scripts and inline event handlers; styles stay
// inline-allowed (React style props), images/tiles/fetch may come from any https host (configurable tile servers, NASA GIBS).
const CSP = [
  "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob: https:",
  "font-src 'self' data:", "connect-src 'self' https:", "worker-src 'self' blob:", "child-src 'self' blob:",
  "object-src 'none'", "base-uri 'self'", "form-action 'self'",
].join('; ');
const csp = (): Plugin => ({
  name: 'thermotrace-csp',
  apply: 'build',
  transformIndexHtml: html => html.replace('<meta charset="UTF-8" />', `<meta charset="UTF-8" />
    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
});

export default defineConfig({
  plugins: [react(), csp()],
  server: {
    host: process.env.VITE_HOST ?? 'localhost',   // set VITE_HOST=0.0.0.0 to expose the dev server on the network
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': '/src',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
