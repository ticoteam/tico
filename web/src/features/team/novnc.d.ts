// @novnc/novnc ships plain ES modules with no types: the parts of its viewer (core/rfb.js) the Screen tab uses.
declare module '@novnc/novnc' {
  export default class RFB extends EventTarget {
    constructor(target: Element, urlOrChannel: string | WebSocket, options?: { shared?: boolean });
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    background: string;
    focusOnClick: boolean;
    disconnect(): void;
    focus(): void;
  }
}
