// Minimal receive-side repair for the vendored SSTP TCP bridge.
// RFC 9293: deliver only contiguous, non-duplicate bytes; FIN uses sequence space.
// This is not a complete TCP stack (see DEPLOYMENT.md for remaining limitations).
const distance = (a, b) => (a - b) | 0;

export class TcpReceiveQueue {
  constructor(next, window = 65535) {
    this.next = next >>> 0;
    this.window = window;
    this.pending = [];
    this.buffered = 0;
    this.fin = null;
    this.closed = false;
  }

  push(sequence, payload, fin = false) {
    const chunks = [];
    if (this.closed) return {chunks, next: this.next, closed: true};
    let offset = distance(sequence, this.next);
    const finPosition = (sequence + payload.length) >>> 0;
    const finOffset = distance(finPosition, this.next);
    if (fin && finOffset >= 0 && finOffset < this.window && this.fin === null)
      this.fin = finPosition;
    // Clip old retransmissions and data outside the advertised receive window.
    if (offset < 0) {
      payload = payload.subarray(Math.min(payload.length, -offset));
      sequence = this.next;
      offset = 0;
    }
    let limit = this.window - offset;
    if (this.fin !== null) limit = Math.min(limit, distance(this.fin, sequence));
    payload = payload.subarray(0, Math.max(0, limit));
    if (payload.length && offset < this.window) {
      if (offset === 0) {
        chunks.push(new Uint8Array(payload));
        this.next = (this.next + payload.length) >>> 0;
      } else {
        const previous = this.pending.find(p => p.sequence === (sequence >>> 0));
        const additional = payload.length - (previous?.bytes.length || 0);
        if (additional > 0 && this.buffered + additional <= this.window &&
            (previous || this.pending.length < 128)) {
          if (previous) previous.bytes = new Uint8Array(payload);
          else this.pending.push({sequence: sequence >>> 0, bytes: new Uint8Array(payload)});
          this.buffered += additional;
        }
      }
    }
    this.pending.sort((a, b) => distance(a.sequence, this.next) - distance(b.sequence, this.next));
    while (this.pending.length && distance(this.pending[0].sequence, this.next) <= 0) {
      const {sequence: seq, bytes} = this.pending.shift();
      this.buffered -= bytes.length;
      const skip = -distance(seq, this.next);
      let tail = bytes.subarray(Math.min(skip, bytes.length));
      if (this.fin !== null) tail = tail.subarray(0, Math.max(0, distance(this.fin, this.next)));
      if (tail.length) {
        chunks.push(tail);
        this.next = (this.next + tail.length) >>> 0;
      }
    }
    if (this.fin !== null && this.next === this.fin) {
      this.next = (this.next + 1) >>> 0;
      this.closed = true;
      this.pending = [];
      this.buffered = 0;
    }
    return {chunks, next: this.next, closed: this.closed};
  }
}

export function parseIpv4Tcp(packet, sourcePort, targetPort, sourceAddress, destinationAddress) {
  if (packet.length < 40 || packet[0] >> 4 !== 4 || packet[9] !== 6) return null;
  const view = new DataView(packet.buffer, packet.byteOffset, packet.byteLength);
  const ipLength = view.getUint16(2);
  const ipHeader = (packet[0] & 15) * 4;
  // Fragment reassembly is deliberately not attempted by this lightweight bridge.
  if (ipHeader < 20 || ipLength > packet.length || ipLength < ipHeader + 20 ||
      (view.getUint16(6) & 0x3fff)) return null;
  if (view.getUint16(ipHeader) !== targetPort || view.getUint16(ipHeader + 2) !== sourcePort) return null;
  for (let i = 0; i < 4; i++) {
    if (packet[12 + i] !== destinationAddress[i] || packet[16 + i] !== sourceAddress[i]) return null;
  }
  const tcpHeader = (packet[ipHeader + 12] >> 4) * 4;
  if (tcpHeader < 20 || ipHeader + tcpHeader > ipLength) return null;
  return {
    flags: packet[ipHeader + 13], sequence: view.getUint32(ipHeader + 4),
    acknowledgment: view.getUint32(ipHeader + 8),
    payloadOffset: ipHeader + tcpHeader, payloadEnd: ipLength,
  };
}

export function sstpControlReply(body) {
  if (body.length < 4) return null;
  const type = (body[0] << 8) | body[1];
  if (type !== 8 && type !== 6) return null;
  return new Uint8Array([0x10, 1, 0, 8, 0, type === 8 ? 9 : 7, 0, 0]);
}

export function pppEchoReply(raw) {
  if (raw.length < 10 || raw[0] !== 0xc0 || raw[1] !== 0x21 || raw[2] !== 9) return null;
  const length = (raw[4] << 8) | raw[5];
  if (length < 8 || length + 2 > raw.length) return null;
  const reply = raw.slice(0, length + 2);
  reply[2] = 10;
  // The pinned implementation doesn't negotiate a local Magic-Number (RFC 1661).
  reply.fill(0, 6, 10);
  return reply;
}
