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
    paths: { base: '/app' }
  }
};
