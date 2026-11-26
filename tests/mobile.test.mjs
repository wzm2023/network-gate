import test from 'node:test';
import assert from 'node:assert/strict';
import {karingConfig, karingYaml} from '../workers/policy.mjs';

test('Karing subscription uses supported url-test groups and stable node identities', () => {
  const nodes = ['a', 'b'].map(host => ({host: host + '.opengw.net', port: 443, residential: 'residential',
    exit: {country_code: 'JP', ip: '60.73.52.173', org: 'Example ISP', is_datacenter: false}}));
  const config = karingConfig(nodes, 'gate.test', 'uuid');
  assert.equal(config['proxy-groups'][0].type, 'select');
  assert.equal(config['proxy-groups'][1].type, 'url-test');
  assert.equal(config['proxy-groups'].some(g => g.type === 'fallback'), false);
  assert.match(config['proxy-groups'][1].url, /^https:\/\/www\.google\.com\//);
  assert.equal(config.proxies.every(p => p.udp === false && p.tls && p['skip-cert-verify'] === false), true);
  const reordered = karingConfig([...nodes].reverse(), 'gate.test', 'uuid');
  assert.equal(config.proxies[0].name, reordered.proxies[1].name);
  assert.equal(config.proxies[0]['ws-opts'].path, reordered.proxies[1]['ws-opts'].path);
  assert.equal('dns' in config, false, 'Karing manages DNS in the app, not this subscription');
  assert.equal('tun' in config, false);
});

test('mobile YAML begins with the Clash discriminator and preserves escaped flow values', () => {
  const nodes = [{host: 'a.opengw.net', port: 443, residential: 'residential', exit: {country_code: 'JP'}}];
  const expected = karingConfig(nodes, 'gate.test', 'uuid');
  const document = karingYaml(nodes, 'gate.test', 'uuid');
  assert.match(document, /^proxies:\n  - /);
  assert.equal(document.trimStart().startsWith('{'), false);
  const restored = {};
  let section;
  for (const line of document.trimEnd().split('\n')) {
    if (!line.startsWith(' ')) { section = line.slice(0, -1); restored[section] = []; }
    else restored[section].push(JSON.parse(line.slice(4)));
  }
  assert.deepEqual(restored, expected);
  assert.equal(restored.proxies[0]['ws-opts'].path, '/sstp://vpn:vpn@a.opengw.net:443');
});
