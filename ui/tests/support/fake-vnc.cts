// A pretend VNC server behind a Playwright WebSocket route: the RFB 3.8 handshake with no password, then one
// framebuffer update (RRE: a desktop colour and a few rectangles) so noVNC draws a picture. Nothing leaves the test.
const W = 1440, H = 900;
const px = ([r, g, b]) => Buffer.from([r, g, b, 0]);   // noVNC asks for 32-bit pixels, red in the low byte
const u16 = n => { const b = Buffer.alloc(2); b.writeUInt16BE(n); return b; };
const u32 = n => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
const RECTS = [
  [[44, 62, 80], 0, H - 40, W, 40],            // the taskbar
  [[236, 239, 242], 120, 80, 760, 520],         // a browser window
  [[210, 214, 219], 120, 80, 760, 36],
  [[69, 192, 156], 150, 160, 300, 24],
  ...[0, 1, 2, 3, 4, 5].map(i => [[160, 168, 176], 150, 210 + i * 34, 640 - i * 60, 12]),
  [[22, 27, 32], 940, 160, 420, 560],           // a terminal
  ...[0, 1, 2, 3, 4, 5, 6, 7].map(i => [[108, 196, 143], 964, 190 + i * 28, 140 + (i * 53) % 220, 10]),
];
function frame() {
  const body = [u32(RECTS.length), px([31, 41, 51])];
  for (const [color, x, y, w, h] of RECTS) body.push(px(color), u16(x), u16(y), u16(w), u16(h));
  return Buffer.concat([Buffer.from([0, 0]), u16(1), u16(0), u16(0), u16(W), u16(H), u32(2), ...body]);
}
function serverInit(name) {
  const format = Buffer.from([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0]);
  return Buffer.concat([u16(W), u16(H), format, u32(Buffer.byteLength(name)), Buffer.from(name)]);
}
// Serve one socket: `ws` is a Playwright WebSocketRoute. `heard`, when given, gets the message type of each client
// message after the handshake (noVNC flushes each input event on its own: 4 a key, 5 the pointer).
exports.fakeVnc = (ws, name = 'Build server', heard = null) => {
  let buf = Buffer.alloc(0), phase = 0;
  const need = [12, 1, 1];          // client version, chosen security type, ClientInit
  ws.onMessage(message => {
    if (phase === 4 && heard) heard.push((Buffer.isBuffer(message) ? message : Buffer.from(message))[0]);
    buf = Buffer.concat([buf, Buffer.isBuffer(message) ? message : Buffer.from(message)]);
    while (phase < 3 && buf.length >= need[phase]) {
      buf = buf.subarray(need[phase]);
      if (phase === 0) ws.send(Buffer.from([1, 1]));                       // one security type: None
      if (phase === 1) ws.send(u32(0));                                    // security result: OK
      if (phase === 2) ws.send(serverInit(name));
      phase++;
    }
    if (phase === 3 && buf.length) { phase = 4; buf = Buffer.alloc(0); ws.send(frame()); }
  });
  ws.send(Buffer.from('RFB 003.008\n'));
};
// noVNC checks that a socket has its members (send, onmessage, readyState, ...) as own keys or on its class's own
// prototype; Playwright's routed WebSocket keeps them further up its prototype chain. Copy them onto each socket.
// Call after page.routeWebSocket, so it wraps Playwright's socket.
exports.exposeRoutedSockets = page => page.addInitScript(() => {
  const Routed = window.WebSocket;
  const props = ['send', 'close', 'binaryType', 'onerror', 'onmessage', 'onopen', 'protocol', 'readyState'];
  window.WebSocket = new Proxy(Routed, {construct(target, args, newTarget) {
    const ws = Reflect.construct(target, args, newTarget);
    for (const prop of props) {
      let proto = Object.getPrototypeOf(ws), d;
      while (proto && !(d = Object.getOwnPropertyDescriptor(proto, prop))) proto = Object.getPrototypeOf(proto);
      if (!d || Object.prototype.hasOwnProperty.call(ws, prop)) continue;
      Object.defineProperty(ws, prop, typeof d.value === 'function' ? {value: d.value.bind(ws), enumerable: true}
        : {get: d.get && (() => d.get.call(ws)), set: d.set && (v => d.set.call(ws, v)), enumerable: true, configurable: true});
    }
    return ws;
  }});
});
