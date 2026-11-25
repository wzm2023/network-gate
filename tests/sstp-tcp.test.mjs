import test from 'node:test';
import assert from 'node:assert/strict';
import {TcpReceiveQueue, parseIpv4Tcp, sstpControlReply, pppEchoReply} from '../workers/sstp-tcp.mjs';

const bytes = s => new TextEncoder().encode(s);
const output = r => new TextDecoder().decode(Buffer.concat(r.chunks));

test('retransmissions and overlapping segments never duplicate TLS bytes', () => {
  const q = new TcpReceiveQueue(100);
  assert.equal(output(q.push(100, bytes('abcd'))), 'abcd');
  assert.equal(output(q.push(100, bytes('abcd'))), '');
  assert.equal(output(q.push(102, bytes('cdef'))), 'ef');
  assert.equal(q.next, 106);
});

test('out-of-order data is held until the missing prefix arrives', () => {
  const q = new TcpReceiveQueue(100);
  assert.equal(output(q.push(104, bytes('efgh'))), '');
  assert.equal(output(q.push(106, bytes('ghij'))), '');
  assert.equal(q.next, 100);
  assert.equal(output(q.push(100, bytes('abcdef'))), 'abcdefghij');
  assert.equal(q.next, 110);
  assert.equal(q.buffered, 0);
});

test('FIN waits for gaps and consumes exactly one sequence number', () => {
  const q = new TcpReceiveQueue(100);
  assert.equal(q.push(104, bytes('ef'), true).closed, false);
  const result = q.push(100, bytes('abcd'));
  assert.equal(output(result), 'abcdef');
  assert.equal(result.closed, true);
  assert.equal(result.next, 107);
  assert.equal(output(q.push(104, bytes('ef'), true)), '');
  assert.equal(q.next, 107);
});

test('32-bit sequence wraparound preserves ordering and rejects old segments', () => {
  const q = new TcpReceiveQueue(0xfffffffc);
  q.push(0, bytes('efgh'));
  assert.equal(output(q.push(0xfffffffc, bytes('abcd'))), 'abcdefgh');
  assert.equal(q.next, 4);
  assert.equal(output(q.push(0xfffffffe, bytes('cdefgh'))), '');
});

test('bounded window drops far-ahead data and does not grow on duplicates', () => {
  const q = new TcpReceiveQueue(100, 16);
  for (let i = 0; i < 1000; i++) q.push(108, bytes('12345678'));
  assert.equal(q.buffered, 8);
  q.push(1000000, bytes('unbounded'));
  assert.equal(q.buffered, 8);
  assert.equal(output(q.push(100, bytes('abcdefgh'))), 'abcdefgh12345678');
});

test('reordering and duplicate delivery reconstruct a deterministic large stream', () => {
  const expected = Uint8Array.from({length: 200000}, (_, i) => (i * 73) & 255);
  const q = new TcpReceiveQueue(0xffff0000);
  const actual = [];
  const deliver = offset => {
    const r = q.push((0xffff0000 + offset) >>> 0, expected.subarray(offset, Math.min(offset + 1000, expected.length)));
    actual.push(...r.chunks);
  };
  for (let offset = 0; offset < expected.length; offset += 2000) {
    deliver(offset + 1000);
    deliver(offset + 1000);
    deliver(offset);
    deliver(offset);
  }
  assert.deepEqual(Buffer.concat(actual), Buffer.from(expected));
});

test('packet parser respects IP length and rejects fragments and invalid headers', () => {
  const p = new Uint8Array(48);
  const v = new DataView(p.buffer);
  p[0] = 0x45; p[9] = 6; v.setUint16(2, 44);
  p.set([1, 2, 3, 4], 12); p.set([5, 6, 7, 8], 16);
  v.setUint16(20, 443); v.setUint16(22, 1234); p[32] = 0x50;
  const parse = () => parseIpv4Tcp(p, 1234, 443, [5, 6, 7, 8], [1, 2, 3, 4]);
  assert.equal(parse().payloadEnd, 44);
  v.setUint16(6, 0x2000);
  assert.equal(parse(), null);
  v.setUint16(6, 0); p[32] = 0xf0;
  assert.equal(parse(), null);
});

test('SSTP keepalive and disconnect requests receive the correct control reply', () => {
  assert.deepEqual([...sstpControlReply(new Uint8Array([0, 8, 0, 0]))], [16, 1, 0, 8, 0, 9, 0, 0]);
  assert.equal(sstpControlReply(new Uint8Array([0, 9, 0, 0])), null);
  assert.equal(sstpControlReply(new Uint8Array([0])), null);
  assert.equal(sstpControlReply(new Uint8Array([0, 6, 0, 0]))[5], 7);
});

test('PPP echo reply preserves identifier and data but uses our unnegotiated magic zero', () => {
  const request = new Uint8Array([0xc0, 0x21, 9, 42, 0, 10, 1, 2, 3, 4, 12, 34]);
  assert.deepEqual([...pppEchoReply(request)], [0xc0, 0x21, 10, 42, 0, 10, 0, 0, 0, 0, 12, 34]);
  assert.equal(request[2], 9);
  request[5] = 100;
  assert.equal(pppEchoReply(request), null);
});
