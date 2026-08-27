import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

import { chromium } from 'playwright-core';
import { AUTOPLAY_CHROMIUM_ARGS, installAutoplayGuard } from '../autoplay-guard.mjs';

const chromiumCandidates = [
  process.env.CHROMIUM_PATH,
  '/usr/bin/chromium',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean);
const executablePath = chromiumCandidates.find((candidate) => fs.existsSync(candidate));

test('blocks automatic and synthetic playback but permits a real play click', {
  skip: executablePath ? false : 'Chromium executable is unavailable',
  timeout: 30_000,
}, async () => {
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: [...AUTOPLAY_CHROMIUM_ARGS],
  });
  try {
    const context = await browser.newContext();
    await installAutoplayGuard(context);
    const page = await context.newPage();
    const testPage = `
      <button id="play" class="bpx-player-ctrl-play" aria-label="播放">Play</button>
      <video id="video" muted playsinline></video>
      <script>
        const canvas = document.createElement('canvas');
        canvas.width = 16;
        canvas.height = 16;
        const drawing = canvas.getContext('2d');
        let frame = 0;
        setInterval(() => {
          drawing.fillStyle = frame++ % 2 ? '#000' : '#fff';
          drawing.fillRect(0, 0, canvas.width, canvas.height);
        }, 100);
        const video = document.querySelector('#video');
        video.srcObject = canvas.captureStream(5);
        const outcome = (promise) => promise.then(() => 'played', (error) => error.name + ':' + error.message);
        window.autoResult = outcome(video.play());
        document.querySelector('#play').addEventListener('click', (event) => {
          window.clickTrusted = event.isTrusted;
          window.clickResult = outcome(video.play());
        });
      </script>
    `;
    await page.goto(`data:text/html;charset=utf-8,${encodeURIComponent(testPage)}`);

    assert.match(await page.evaluate(() => window.autoResult), /^NotAllowedError:Automatic media playback is disabled$/);
    assert.equal(await page.locator('#video').evaluate((video) => video.paused), true);

    await page.locator('#play').evaluate((button) => button.click());
    assert.match(await page.evaluate(() => window.clickResult), /^NotAllowedError:Automatic media playback is disabled$/);

    await page.locator('#play').click();
    assert.equal(await page.evaluate(() => window.clickTrusted), true);
    assert.equal(await page.evaluate(() => window.clickResult), 'played');
    assert.equal(await page.locator('#video').evaluate((video) => video.paused), false);
  } finally {
    await browser.close();
  }
});
