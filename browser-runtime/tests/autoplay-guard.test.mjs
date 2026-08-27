import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTOPLAY_CHROMIUM_ARGS,
  AUTOPLAY_GUARD_SOURCE,
  AUTOPLAY_GUARD_VERSION,
  installAutoplayGuard,
} from '../autoplay-guard.mjs';

test('uses Chromium gesture policy and disables engagement bypasses', () => {
  assert.ok(AUTOPLAY_CHROMIUM_ARGS.includes('--autoplay-policy=user-gesture-required'));
  assert.ok(AUTOPLAY_CHROMIUM_ARGS.some((value) => value.includes('MediaEngagementBypassAutoplayPolicies')));
});

test('installs the guard for future and already-open pages', async () => {
  const calls = [];
  const context = {
    async addInitScript(value) { calls.push(['init', value]); },
    pages() {
      return [
        { async evaluate(value) { calls.push(['page', value]); } },
        { async evaluate() { throw new Error('chrome:// page'); } },
      ];
    },
  };

  await installAutoplayGuard(context);
  assert.deepEqual(calls[0], ['init', { content: AUTOPLAY_GUARD_SOURCE }]);
  assert.deepEqual(calls[1], ['page', AUTOPLAY_GUARD_SOURCE]);
});

test('guard only accepts trusted playback controls', () => {
  assert.match(AUTOPLAY_GUARD_SOURCE, /event\.isTrusted/);
  assert.match(AUTOPLAY_GUARD_SOURCE, /Automatic media playback is disabled/);
  assert.match(AUTOPLAY_GUARD_SOURCE, /gestureBudget = 1/);
  assert.match(AUTOPLAY_GUARD_SOURCE, new RegExp(AUTOPLAY_GUARD_VERSION.replaceAll('.', '\\.') ));
});
