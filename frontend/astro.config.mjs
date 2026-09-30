// @ts-check
import { fileURLToPath } from 'node:url';
import { defineConfig, envField } from 'astro/config';
import { loadEnv } from 'vite';

import node from '@astrojs/node';
import tailwindcss from '@tailwindcss/vite';

// Single .env at the repository root, shared with the backend.
const envDir = fileURLToPath(new URL('..', import.meta.url));
const rootEnv = loadEnv(process.env.NODE_ENV ?? 'development', envDir, '');
const apiInternalUrl = rootEnv.API_INTERNAL_URL ?? 'http://127.0.0.1:8000';

// https://astro.build/config
export default defineConfig({
  output: 'server',

  adapter: node({
    mode: 'standalone'
  }),

  // Astro sessions are not used: sessions live in FastAPI (MySQL + HttpOnly cookie).
  session: false,

  env: {
    schema: {
      // Read at runtime on the server only; never exposed to the browser.
      API_INTERNAL_URL: envField.string({ context: 'server', access: 'secret', url: true })
    }
  },

  vite: {
    envDir,
    plugins: [tailwindcss()],
    server: {
      // Development same-origin strategy: the browser only talks to Astro,
      // and /api/v1 is forwarded to FastAPI. Production uses a reverse proxy.
      proxy: {
        '/api/v1': {
          target: apiInternalUrl,
          changeOrigin: true
        }
      }
    }
  }
});
