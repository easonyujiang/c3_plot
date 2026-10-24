import { defineConfig, loadEnv } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL('..', import.meta.url)), 'C3_');
  const target = env.C3_API_URL || `http://127.0.0.1:${env.C3_MASS_PORT || '8765'}`;
  return {
    base: '/workspace/',
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      rollupOptions: {
        onwarn(warning, warn) {
          // React packages also support server components; this SPA uses their client exports.
          if (warning.code === 'MODULE_LEVEL_DIRECTIVE' && warning.message.includes('use client')) return;
          warn(warning);
        },
      },
    },
    server: {host: '127.0.0.1', port: 5173, strictPort: true, proxy: {'/api': target, '/static': target}},
  };
});
