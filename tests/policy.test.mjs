import test from 'node:test';
import assert from 'node:assert/strict';
import {authorized, validGateAuthority, freshNodes, proxyEntries, clashConfig, vlessLinks, residentialCandidates, subscriptionMetadata} from '../workers/policy.mjs';

test('authentication fails closed and query tokens require explicit permission', () => {
  const secret = 'x'.repeat(40);
  assert.equal(authorized(new Request('https://unit.test/'), undefined), false);
  assert.equal(authorized(new Request(`https://unit.test/?token=${secret}`), secret), false);
  assert.equal(authorized(new Request(`https://unit.test/?token=${secret}`), secret, true), true);
  assert.equal(authorized(new Request('https://unit.test/', {headers: {Authorization: `Bearer ${secret}`}}), secret), true);
});

test('checker targets are restricted to VPN Gate domains and valid ports', () => {
  assert.equal(validGateAuthority('vpn:vpn@public-vpn-1.opengw.net:443'), true);
  for (const target of ['vpn:vpn@127.0.0.1:80', 'vpn:vpn@opengw.net.evil.com:443', 'vpn:vpn@a.opengw.net:0', 'vpn:vpn@a.opengw.net:99999'])
    assert.equal(validGateAuthority(target), false);
});

test('expired results fail closed; unsafe and duplicate nodes are removed', () => {
  const now = Date.now();
  const n = {host: 'a.opengw.net', port: 443, success: true, latency_ms: 50};
  const data = {generated_at: new Date(now - 1000).toISOString(), expires_at: new Date(now + 60000).toISOString(), countries: {JP: {nodes: [n, n, {...n, host: 'localhost'}, {...n, host: 'b.opengw.net', exit: {is_abuser: true}}]}}};
  assert.equal(freshNodes(data, now).length, 1);
  assert.throws(() => freshNodes({...data, expires_at: new Date(now - 1).toISOString()}, now), /stale/);
  assert.throws(() => freshNodes({...data, generated_at: null}, now), /stale/);
});

test('client configuration is loopback only, has no TUN or system DNS, and explicit SSTP paths', () => {
  const proxies = proxyEntries([{host: 'a.opengw.net', port: 443, country_code: 'JP'}], 'gate.test', 'test-uuid');
  const config = clashConfig(proxies);
  assert.equal(config['bind-address'], '127.0.0.1');
  assert.equal(config['mixed-port'], 17898);
  assert.equal(config.tun.enable, false);
  assert.equal(config.dns.enable, false);
  assert.equal(config.proxies[0]['skip-cert-verify'], false);
  assert.match(proxies[0]['ws-opts'].path, /^\/sstp:\/\//);
  assert.equal(config['proxy-groups'][1].type, 'fallback');
  assert.match(vlessLinks(proxies), /^vless:\/\//);
});

test('residential pool excludes unknown hosting, datacenter, abuse and missing evidence', () => {
  const home = {host: 'a.opengw.net', port: 443, residential: 'residential',
    exit: {ip: '60.73.52.173', org: 'Example ISP', is_datacenter: false}};
  const nodes = [home, {...home, residential: 'unknown'}, {...home, exit: {...home.exit, is_datacenter: null}},
    {...home, exit: {...home.exit, is_datacenter: true}}, {...home, exit: {...home.exit, is_abuser: true}}];
  assert.deepEqual(residentialCandidates(nodes), [home]);
  const proxies = proxyEntries([home], 'gate.test', 'uuid');
  const metadata = subscriptionMetadata({generated_at: 'created', expires_at: 'expires'}, [home], proxies, true);
  assert.equal(metadata.mode, 'residential-candidates');
  assert.equal(metadata.nodes[proxies[0].name].exit_ip, home.exit.ip);
});
