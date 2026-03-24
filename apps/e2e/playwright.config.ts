import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3000',
    screenshot: 'on',
    video: 'on-first-retry',
    trace: 'retain-on-failure',
  },
  reporter: [
    ['json', { outputFile: 'results/results.json' }],
    ['list'],
  ],
  outputDir: 'results/artifacts',
});
