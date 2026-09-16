import adapter from '@sveltejs/adapter-static';

export default {
  kit: {
    adapter: adapter({
      pages: '../pos/static/frontend',
      assets: '../pos/static/frontend',
      fallback: 'index.html',
      precompress: false,
      strict: false
    }),
    paths: { base: '/app' },
    // Django serves the generated SPA fallback for arbitrary nested routes
    // such as /app/sales/39. Those runtime URLs cannot be discovered by the
    // static prerender crawler, so they are intentionally handled at runtime.
    prerender: { handleUnseenRoutes: 'ignore' }
  }
};
