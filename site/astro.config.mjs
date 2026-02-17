// @ts-check

import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';

// https://astro.build/config
export default defineConfig({
	site: 'https://magi127605-cmd.github.io',
	base: '/autonomous-earner',
	integrations: [mdx(), sitemap()],
});
