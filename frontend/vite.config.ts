import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/** Preload the font files that matter: the Latin ones always, the Arabic one only when the stored language is Persian. */
function fontPreload(): Plugin {
  return {
    name: 'font-preload',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.bundle) return html;
        const find = (n: string) => Object.keys(ctx.bundle!).find((k) => k.includes(n) && k.endsWith('.woff2'));
        const link = (f: string) => `<link rel="preload" as="font" type="font/woff2" crossorigin href="/${f}">`;
        const always = ['sora-latin', 'jetbrains-mono-latin'].map(find).filter(Boolean) as string[];
        const fa = ['vazirmatn-arabic', 'vazirmatn-latin'].map(find).filter(Boolean) as string[];
        const script = `<script>(function(){try{if(localStorage.getItem('lang')==='fa'){${fa.map((f) => `var l=document.createElement('link');l.rel='preload';l.as='font';l.type='font/woff2';l.crossOrigin='';l.href='/${f}';document.head.appendChild(l);`).join('')}}}catch(e){}})();</script>`;
        return html.replace('</head>', always.map(link).join('') + script + '</head>');
      },
    },
  };
}

export default defineConfig({
  plugins: [react(), fontPreload()],
  server: { port: 5173, proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } } },
  build: { outDir: 'dist', emptyOutDir: true, sourcemap: false, assetsInlineLimit: 0 },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
