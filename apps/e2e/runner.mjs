#!/usr/bin/env node
/**
 * E2E Test Suite Runner
 *
 * Fetches saved tests from the Norm API, writes them as Playwright test files,
 * runs them, and reports results back to the API.
 *
 * Usage:
 *   BASE_URL=https://testing.bettercallnorm.com \
 *   API_URL=https://testing.bettercallnorm.com \
 *   API_TOKEN=<jwt> \
 *   node runner.mjs
 *
 * Or with explicit args:
 *   node runner.mjs --base-url https://... --api-url https://... --api-token <jwt>
 */

import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync, rmSync } from 'fs';
import { resolve, join } from 'path';

// ── Parse args ──────────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(name, envVar) {
  const idx = args.indexOf(`--${name}`);
  if (idx !== -1 && args[idx + 1]) return args[idx + 1];
  return process.env[envVar] || '';
}

const BASE_URL = getArg('base-url', 'BASE_URL');
const API_URL = getArg('api-url', 'API_URL') || BASE_URL;
const API_TOKEN = getArg('api-token', 'API_TOKEN');
const ENVIRONMENT = getArg('environment', 'ENVIRONMENT') || 'testing';
const GIT_SHA = getArg('git-sha', 'GIT_SHA') || 'unknown';

if (!BASE_URL) {
  console.error('ERROR: BASE_URL is required (--base-url or $BASE_URL)');
  process.exit(1);
}

console.log(`E2E Runner: ${BASE_URL} (${ENVIRONMENT})`);

// ── Fetch saved tests from API ──────────────────────────────────
async function fetchTests() {
  const url = `${API_URL}/api/admin/tests`;
  const headers = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers['Authorization'] = `Bearer ${API_TOKEN}`;

  try {
    const res = await fetch(url, { headers });
    if (!res.ok) {
      console.error(`Failed to fetch tests: ${res.status} ${res.statusText}`);
      return [];
    }
    const data = await res.json();
    return (data.tests || []).filter(t => t.playwright_script);
  } catch (err) {
    console.error(`Failed to fetch tests from ${url}:`, err.message);
    return [];
  }
}

// ── Report results back to API ──────────────────────────────────
async function reportResults(results) {
  const url = `${API_URL}/api/admin/test-runs/webhook`;
  const headers = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers['Authorization'] = `Bearer ${API_TOKEN}`;

  try {
    await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        environment: ENVIRONMENT,
        git_sha: GIT_SHA,
        status: results.failed === 0 ? 'passed' : 'failed',
        total: results.total,
        passed: results.passed,
        failed: results.failed,
        duration_ms: results.duration_ms,
        test_results: results.tests,
      }),
    });
    console.log('Results reported to API');
  } catch (err) {
    console.warn('Failed to report results:', err.message);
  }
}

// ── Main ────────────────────────────────────────────────────────
async function main() {
  const testsDir = resolve('./tests');
  const resultsDir = resolve('./results');

  // Clean previous runs
  if (existsSync(testsDir)) rmSync(testsDir, { recursive: true });
  if (existsSync(resultsDir)) rmSync(resultsDir, { recursive: true });
  mkdirSync(testsDir, { recursive: true });
  mkdirSync(resultsDir, { recursive: true });

  // Fetch tests
  const tests = await fetchTests();
  if (tests.length === 0) {
    console.log('No saved tests found. Skipping E2E run.');
    process.exit(0);
  }

  console.log(`Found ${tests.length} saved test(s)`);

  // Write each test as a Playwright test file
  for (const [i, test] of tests.entries()) {
    const filename = `test_${i + 1}_${test.name.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase()}.spec.ts`;
    writeFileSync(join(testsDir, filename), test.playwright_script);
    console.log(`  Written: ${filename}`);
  }

  // Run Playwright
  const startTime = Date.now();
  let exitCode = 0;
  try {
    execSync(`npx playwright test --reporter=json,list`, {
      cwd: resolve('.'),
      stdio: 'inherit',
      env: {
        ...process.env,
        BASE_URL,
        PLAYWRIGHT_JSON_OUTPUT_NAME: 'results/results.json',
      },
    });
  } catch (err) {
    exitCode = err.status || 1;
  }
  const duration_ms = Date.now() - startTime;

  // Parse results
  let passed = 0;
  let failed = 0;
  let testResults = [];

  const resultsFile = resolve('results/results.json');
  if (existsSync(resultsFile)) {
    try {
      const raw = JSON.parse(readFileSync(resultsFile, 'utf-8'));
      for (const suite of raw.suites || []) {
        for (const spec of suite.specs || []) {
          const status = spec.ok ? 'passed' : 'failed';
          if (spec.ok) passed++;
          else failed++;
          testResults.push({
            name: spec.title,
            status,
            duration_ms: spec.tests?.[0]?.results?.[0]?.duration || 0,
            error: spec.ok ? null : spec.tests?.[0]?.results?.[0]?.error?.message || 'Unknown error',
          });
        }
      }
    } catch {
      console.warn('Could not parse results JSON');
    }
  }

  const total = passed + failed;
  console.log(`\nResults: ${passed}/${total} passed, ${failed} failed (${duration_ms}ms)`);

  // Report results back to API
  await reportResults({ total, passed, failed, duration_ms, tests: testResults });

  process.exit(exitCode);
}

main();
