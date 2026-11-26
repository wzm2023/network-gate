import test from 'node:test';
import assert from 'node:assert/strict';
import checker from '../workers/checker.mjs';
import edge from '../workers/edge.mjs';

const secret = 'x'.repeat(40);
const request = (path, options = {}) => new Request('https://gate.test' + path, options);

test('deployed handlers reject absent credentials before opening connections', async () => {
  assert.equal((await checker.fetch(request('/check?sstp=vpn:vpn@a.opengw.net:443'), {})).status, 401);
  assert.equal((await edge.fetch(request('/subscription/clash'), {}, {})).status, 401);
  assert.equal((await edge.fetch(request('/publish', {method: 'POST', body: '{}'}), {}, {})).status, 401);
  assert.equal((await edge.fetch(request('/discovery'), {}, {})).status, 401);
  assert.equal((await edge.fetch(request('/check'), {}, {})).status, 401);
});

test('checker rejects arbitrary targets even with a correct token', async () => {
  const res = await checker.fetch(request('/check?sstp=vpn:vpn@localhost:80', {headers: {Authorization: `Bearer ${secret}`}}), {CHECK_TOKEN: secret});
  assert.equal(res.status, 400);
});

test('edge rejects direct routes and rejects stale or too-small publications', async () => {
  assert.equal((await edge.fetch(request('/', {method: 'POST', body: 'test'}), {}, {})).status, 400);
  const env = {PUBLISH_TOKEN: secret, KV: {get: async () => null, put: async () => assert.fail('Must not publish invalid data')}};
  for (const data of [{}, {generated_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), countries: {}}]) {
    const res = await edge.fetch(request('/publish', {method: 'POST', headers: {Authorization: `Bearer ${secret}`}, body: JSON.stringify(data)}), env, {});
    assert.equal(res.status, 422);
  }
});

test('native subscription stays local and keeps credentials out of public status', async () => {
  const data = {generated_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), countries: {JP: {nodes: [{host: 'a.opengw.net', port: 443, success: true, latency_ms: 50}]}}};
  const env = {SUB_TOKEN: secret, UUID: 'uuid-test', HOST: 'gate.test', KV: {get: async () => data}};
  const res = await edge.fetch(request('/subscription/clash', {headers: {Authorization: `Bearer ${secret}`}}), env, {});
  const cfg = await res.json();
  assert.equal(cfg.tun.enable, false);
  assert.equal(cfg.proxies.length, 1);
  assert.equal(cfg.proxies[0].uuid, env.UUID);
  assert.match(res.headers.get('Cache-Control'), /no-store/);
});

test('strict subscriptions contain only residential candidates and fail closed with an empty pool', async () => {
  const nodes = ['home', 'unknown', 'hosting'].map(host => ({host: host + '.opengw.net', port: 443, success: true,
    residential: host === 'home' ? 'residential' : host === 'hosting' ? 'datacenter' : 'unknown',
    exit: {ip: '60.73.52.173', org: 'Example ISP', is_datacenter: host === 'hosting'}}));
  const data = {generated_at: new Date().toISOString(), expires_at: new Date(Date.now() + 60000).toISOString(), countries: {JP: {nodes}}};
  const env = {SUB_TOKEN: secret, PUBLISH_TOKEN: secret, UUID: 'uuid-test', HOST: 'gate.test', RESIDENTIAL_ONLY: 'true',
    KV: {get: async () => null, put: async () => {}}};
  const headers = {Authorization: `Bearer ${secret}`};
  const publish = () => edge.fetch(request('/publish', {method: 'POST', headers, body: JSON.stringify(data)}), env, {});
  assert.equal((await publish()).status, 200);
  const res = await edge.fetch(request('/subscription/clash', {headers}), env, {});
  assert.equal(res.status, 200);
  const cfg = await res.json();
  assert.equal(cfg.proxies.length, 1);
  assert.equal(cfg['x-gate'].mode, 'residential-candidates');
  assert.equal(cfg['x-gate'].nodes[cfg.proxies[0].name].candidate, true);
  const mobile = await edge.fetch(request('/subscription/karing', {headers}), env, {});
  assert.equal(mobile.status, 200);
  assert.match(mobile.headers.get('Content-Type'), /application\/yaml/);
  const mobileText = await mobile.text();
  assert.match(mobileText, /^proxies:\n/);
  assert.equal(mobileText.trimStart().startsWith('{'), false, 'Karing 1.2.25 routes JSON-shaped Clash to the wrong importer');
  assert.match(mobileText, /^proxy-groups:/m);
  assert.match(mobileText, /"type":"url-test"/);
  nodes[0].residential = 'unknown';
  assert.equal((await publish()).status, 200);
  const empty = await edge.fetch(request('/subscription/clash', {headers}), env, {});
  assert.equal(empty.status, 503);
  assert.equal((await empty.json()).error, 'no_residential_candidates');
  assert.equal((await edge.fetch(request('/subscription/karing', {headers}), env, {})).status, 503);
});
