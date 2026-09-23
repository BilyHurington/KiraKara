import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The build is served by `kara-align serve` from kara_align/web/static.
// `npm run dev` proxies the API to a running server (default port 8799).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': new URL('./src', import.meta.url).pathname } },
  base: './',
  build: {
    outDir: '../kara_align/web/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
  server: {
    proxy: { '/api': `http://127.0.0.1:${process.env.KARA_API_PORT || 8799}` },
  },
});
