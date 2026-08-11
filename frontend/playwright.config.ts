import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: 'html',
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command:
        'uv run --project .. uvicorn app.main:app --app-dir .. --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/healthz',
      env: {
        TREASURY_APP_ENV: 'test',
        TREASURY_DATABASE_PATH: '.data/e2e-matching.sqlite3',
        TREASURY_TEMP_DIR: '.data/e2e-matching-tmp',
        TREASURY_FAKE_EXTRACTION_SCENARIO: 'clear_matching_label',
      },
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173',
      url: 'http://127.0.0.1:5173',
      env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:8000' },
      reuseExistingServer: false,
    },
    {
      command:
        'uv run --project .. uvicorn app.main:app --app-dir .. --host 127.0.0.1 --port 8001',
      url: 'http://127.0.0.1:8001/healthz',
      env: {
        TREASURY_APP_ENV: 'test',
        TREASURY_DATABASE_PATH: '.data/e2e-mismatch.sqlite3',
        TREASURY_TEMP_DIR: '.data/e2e-mismatch-tmp',
        TREASURY_FAKE_EXTRACTION_SCENARIO: 'mismatched_net_contents',
      },
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5174',
      url: 'http://127.0.0.1:5174',
      env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:8001' },
      reuseExistingServer: false,
    },
  ],
})
