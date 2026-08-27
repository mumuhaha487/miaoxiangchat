declare module '@novnc/novnc/lib/rfb.js' {
  export default class RFB {
    constructor(target: HTMLElement, url: string, options?: { credentials?: { password?: string } });
    scaleViewport: boolean;
    clipViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    background: string;
    focus(): void;
    disconnect(): void;
    addEventListener(name: string, callback: (event: Event) => void): void;
  }
}
