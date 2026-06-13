import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import AutoImport from 'unplugin-auto-import/vite';
import Components from 'unplugin-vue-components/vite';
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers';
import path from 'node:path';

// MindEngine frontend — local dev runs against `uvicorn` on :8000.
// `VITE_API_BASE` overrides the hard default for prod deployments.
//
// Element Plus is imported ON-DEMAND (not the full library) via the two
// unplugin resolvers below. This is the single biggest lever for build
// memory: full import forces Rollup to hold the entire component graph,
// which OOMs small (2 GB) build hosts. On-demand keeps peak RAM low and
// shrinks the bundle. Generated type shims land in src/types/*.d.ts.

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      dts: 'src/types/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/types/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // SSE pass-through. Without `changeOrigin` the cookie/host header
      // mismatch trips uvicorn's strict host check.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/healthz': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // Docker/ECS builds skip sourcemaps to cut memory and time.
    sourcemap: process.env.DOCKER_BUILD !== '1',
    // esbuild minify is far lighter on RAM than terser (Vite default, but
    // pinned here so a future config tweak doesn't silently regress builds
    // on small hosts).
    minify: 'esbuild',
    chunkSizeWarningLimit: 1500,
  },
});
