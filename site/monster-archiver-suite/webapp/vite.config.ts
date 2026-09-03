import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    build: {
      // Split the heavy, rarely-changing vendors out of the single app
      // bundle: they cache across deploys (a 529 kB monolith forced a full
      // re-download on every UI edit) and download in parallel. No runtime
      // behavior change.
      rollupOptions: {
        output: {
          manualChunks: {
            'vendor-react': ['react', 'react-dom'],
            'vendor-motion': ['motion'],
            'vendor-wavesurfer': ['wavesurfer.js'],
            'vendor-icons': ['lucide-react'],
          },
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // Hosts beyond localhost allowed to reach the dev middleware. Keeps
      // sandbox/preview proxies (any *.e2b.app host) working when HOST is
      // 0.0.0.0; Vite's default host check would otherwise answer 403
      // "Blocked request. This host is not allowed". The app itself still
      // binds to 127.0.0.1 by default, so nothing is reachable remotely
      // unless the user opts in via HOST.
      allowedHosts: ['.e2b.app'],
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify— file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
