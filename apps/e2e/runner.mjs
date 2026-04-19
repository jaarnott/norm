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
// The webhook updates one E2ETestRun per call, keyed by test_id — so we
// post once per test rather than an aggregate.
async function reportResults(testResults) {
  const url = `${API_URL}/api/admin/test-runs/webhook`;
  const headers = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers['Authorization'] = `Bearer ${API_TOKEN}`;

  for (const r of testResults) {
    try {
      await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          test_id: r.test_id,
          environment: ENVIRONMENT,
          status: r.status,
          duration_ms: r.duration_ms,
          error_message: r.error,
          stdout: r.stdout,
          git_sha: GIT_SHA,
        }),
      });
    } catch (err) {
      console.warn(`Failed to report result for ${r.name}:`, err.message);
    }
  }
  console.log(`Results reported to API (${testResults.length} test${testResults.length === 1 ? '' : 's'})`);
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

  // Write each test as a Playwright test file; track filename → test_id
  const filenameToTestId = {};
  for (const [i, test] of tests.entries()) {
    const filename = `test_${i + 1}_${test.name.replace(/[^a-zA-Z0-9]/g, '_').toLowerCase()}.spec.ts`;
    writeFileSync(join(testsDir, filename), test.playwright_script);
    filenameToTestId[filename] = test.id;
    console.log(`  Written: ${filename}`);
  }

  // Run Playwright, capturing stdout so we can show it per-test in the UI
  const startTime = Date.now();
  let exitCode = 0;
  let playwrightOutput = '';
  try {
    playwrightOutput = execSync(`npx playwright test --reporter=json,list`, {
      cwd: resolve('.'),
      env: {
        ...process.env,
        BASE_URL,
        PLAYWRIGHT_JSON_OUTPUT_NAME: 'results/results.json',
        FORCE_COLOR: '0',
      },
      encoding: 'utf-8',
      maxBuffer: 10 * 1024 * 1024,
    });
    console.log(playwrightOutput);
  } catch (err) {
    exitCode = err.status || 1;
    playwrightOutput = (err.stdout || '') + (err.stderr || '');
    console.log(playwrightOutput);
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
        // Each suite corresponds to one spec file; map it back to the saved test
        const specFile = suite.file || '';
        const filename = specFile.split('/').pop();
        const testId = filenameToTestId[filename] || null;
        for (const spec of suite.specs || []) {
          const status = spec.ok ? 'passed' : 'failed';
          if (spec.ok) passed++;
          else failed++;
          const result = spec.tests?.[0]?.results?.[0];
          // Grab the portion of Playwright's output that references this test file.
          const specLogLines = playwrightOutput
            .split('\n')
            .filter(line => !filename || line.includes(filename) || line.includes(spec.title));
          const consoleOutput = [
            ...(result?.stdout || []).map(e => `[stdout] ${e.text || ''}`),
            ...(result?.stderr || []).map(e => `[stderr] ${e.text || ''}`),
          ].join('');
          const errors = (result?.errors || [])
            .map(e => e.message || e.stack || '').filter(Boolean).join('\n\n');
          const combined = [specLogLines.join('\n'), errors, consoleOutput].filter(Boolean).join('\n\n');
          testResults.push({
            test_id: testId,
            name: spec.title,
            status,
            duration_ms: result?.duration || 0,
            error: spec.ok ? null : result?.error?.message || errors || 'Unknown error',
            stdout: combined || null,
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
  await reportResults(testResults);

  process.exit(exitCode);
}

main();
