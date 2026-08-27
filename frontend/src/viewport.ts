const KEYBOARD_THRESHOLD_PX = 120;

function applyViewportMetrics() {
  const viewport = window.visualViewport;
  const height = Math.max(1, Math.round(viewport?.height ?? window.innerHeight));
  const obscuredHeight = Math.max(0, window.innerHeight - height - Math.round(viewport?.offsetTop ?? 0));
  const root = document.documentElement;
  const keyboardVisible = obscuredHeight >= KEYBOARD_THRESHOLD_PX;
  root.style.setProperty('--app-viewport-height', `${height}px`);
  root.dataset.keyboardVisible = keyboardVisible ? 'true' : 'false';
  window.dispatchEvent(new CustomEvent('vmss-viewport-change', {
    detail: { height, keyboardVisible },
  }));
}

export function installViewportSizing() {
  let frame = 0;
  const update = () => {
    window.cancelAnimationFrame(frame);
    frame = window.requestAnimationFrame(applyViewportMetrics);
  };
  const updateAfterKeyboardTransition = () => {
    update();
    window.setTimeout(update, 80);
    window.setTimeout(update, 280);
  };

  update();
  window.addEventListener('resize', update);
  window.addEventListener('orientationchange', updateAfterKeyboardTransition);
  window.addEventListener('focusin', updateAfterKeyboardTransition);
  window.addEventListener('focusout', updateAfterKeyboardTransition);
  window.visualViewport?.addEventListener('resize', update);
  window.visualViewport?.addEventListener('scroll', update);

  return () => {
    window.cancelAnimationFrame(frame);
    window.removeEventListener('resize', update);
    window.removeEventListener('orientationchange', updateAfterKeyboardTransition);
    window.removeEventListener('focusin', updateAfterKeyboardTransition);
    window.removeEventListener('focusout', updateAfterKeyboardTransition);
    window.visualViewport?.removeEventListener('resize', update);
    window.visualViewport?.removeEventListener('scroll', update);
  };
}
