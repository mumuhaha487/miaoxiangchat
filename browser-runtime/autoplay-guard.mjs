export const AUTOPLAY_GUARD_VERSION = '1.0.0';

export const AUTOPLAY_CHROMIUM_ARGS = Object.freeze([
  '--autoplay-policy=user-gesture-required',
  '--disable-features=PreloadMediaEngagementData,MediaEngagementBypassAutoplayPolicies,Translate,OptimizationHints',
]);

export const AUTOPLAY_GUARD_SOURCE = `(() => {
  const guardVersion = ${JSON.stringify('1.0.0')};
  if (globalThis.__mumuAutoplayGuardVersion === guardVersion) return;

  const MediaElement = globalThis.HTMLMediaElement;
  if (!MediaElement) return;

  const originalPlay = MediaElement.prototype.play;
  const permittedUntil = new WeakMap();
  let gestureDeadline = 0;
  let gestureBudget = 0;

  const now = () => globalThis.performance?.now?.() ?? Date.now();
  const hasPermit = (media) => (permittedUntil.get(media) || 0) >= now();
  const consumeGesture = (media) => {
    if (gestureBudget < 1 || now() > gestureDeadline) return false;
    gestureBudget = 0;
    permittedUntil.set(media, now() + 2000);
    return true;
  };

  const isPlaybackControl = (event) => {
    if (!event.isTrusted) return false;
    if (event.type === 'keydown' && ['MediaPlayPause', 'MediaPlay'].includes(event.code)) {
      return true;
    }
    if (event.type === 'keydown' && !['Space', 'Enter'].includes(event.code)) return false;
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [event.target];
    return path.some((node) => {
      if (node instanceof MediaElement) return true;
      if (!(node instanceof Element)) return false;
      const isButton = node.matches('button,[role="button"],[class*="control"],[class*="player"]');
      if (!isButton) return false;
      const signal = [
        node.id,
        node.className,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('data-title'),
      ].filter((value) => typeof value === 'string').join(' ');
      return /(?:^|[\\s_-])(play|pause|video|media|player)(?:$|[\\s_-])|播放|暂停/i.test(signal);
    });
  };

  const rememberGesture = (event) => {
    if (!isPlaybackControl(event)) return;
    gestureDeadline = now() + 1500;
    gestureBudget = 1;
  };

  for (const eventName of ['pointerdown', 'click', 'keydown']) {
    globalThis.addEventListener(eventName, rememberGesture, { capture: true, passive: true });
  }

  Object.defineProperty(MediaElement.prototype, 'play', {
    configurable: true,
    writable: true,
    value: function guardedPlay() {
      if (!hasPermit(this) && !consumeGesture(this)) {
        this.pause();
        return Promise.reject(new DOMException('Automatic media playback is disabled', 'NotAllowedError'));
      }
      return originalPlay.call(this);
    },
  });

  const stopAutomaticPlayback = (media) => {
    if (!(media instanceof MediaElement)) return;
    media.autoplay = false;
    media.removeAttribute('autoplay');
    if (!hasPermit(media) && !media.paused) media.pause();
  };

  const sanitizeTree = (root) => {
    if (root instanceof MediaElement) stopAutomaticPlayback(root);
    if (typeof root.querySelectorAll === 'function') {
      for (const media of root.querySelectorAll('video,audio')) stopAutomaticPlayback(media);
    }
  };

  globalThis.addEventListener('play', (event) => {
    const media = event.target;
    if (!(media instanceof MediaElement) || hasPermit(media)) return;
    if (consumeGesture(media)) return;
    media.pause();
  }, true);

  const startObserver = () => {
    sanitizeTree(document.documentElement);
    new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'attributes') stopAutomaticPlayback(record.target);
        for (const node of record.addedNodes) sanitizeTree(node);
      }
    }).observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['autoplay'],
    });
  };

  if (document.documentElement) startObserver();
  else document.addEventListener('DOMContentLoaded', startObserver, { once: true });
  Object.defineProperty(globalThis, '__mumuAutoplayGuardVersion', {
    configurable: false,
    enumerable: false,
    writable: false,
    value: guardVersion,
  });
})();`;

export async function installAutoplayGuard(context) {
  await context.addInitScript({ content: AUTOPLAY_GUARD_SOURCE });
  await Promise.all(context.pages().map(async (page) => {
    try {
      await page.evaluate(AUTOPLAY_GUARD_SOURCE);
    } catch {
      // Browser-owned pages cannot be scripted; future navigations still receive the init script.
    }
  }));
}
