export function authorized(request, secret, queryAllowed = false) {
  if (typeof secret !== 'string' || secret.length < 32) return false;
  const bearer = request.headers.get('Authorization');
  const candidate = bearer?.startsWith('Bearer ') ? bearer.slice(7) :
    queryAllowed ? new URL(request.url).searchParams.get('token') : null;
  if (typeof candidate !== 'string' || candidate.length !== secret.length) return false;
  let mismatch = 0;
  for (let i = 0; i < secret.length; i++) mismatch |= candidate.charCodeAt(i) ^ secret.charCodeAt(i);
  return mismatch === 0;
}

export function validGateAuthority(value) {
  return /^vpn:vpn@(?:[a-z0-9-]+\.)+(?:opengw\.net|vpngate\.net):[0-9]{1,5}$/i.test(value) &&
    Number(value.split(':').at(-1)) > 0 && Number(value.split(':').at(-1)) <= 65535;
}

export function freshNodes(data, now = Date.now(), maxAge = 7200) {
  const created = Date.parse(data?.generated_at);
  const expires = Date.parse(data?.expires_at);
  if (!Number.isFinite(created) || !Number.isFinite(expires) || created > now + 60000 ||
      expires <= now || now - created > maxAge * 1000) throw new Error('node_list_stale');
  const unique = new Map();
  for (const group of Object.values(data.countries || {})) {
    for (const node of group.nodes || []) {
      const authority = `vpn:vpn@${node.host}:${node.port}`;
      if (node.success !== true || node.excluded_reason || !validGateAuthority(authority)) continue;
      if (node.exit?.is_abuser === true) continue;
      unique.set(authority, node);
    }
  }
  const nodes = [...unique.values()].sort((a, b) => (a.latency_ms ?? Infinity) - (b.latency_ms ?? Infinity));
  if (!nodes.length) throw new Error('no_eligible_nodes');
  return nodes;
}

export function proxyEntries(nodes, host, uuid) {
  return nodes.slice(0, 60).map((n, i) => ({
    name: `${n.exit?.country_code || n.country_code || '??'}-${String(i + 1).padStart(2, '0')}-${n.residential === 'residential' ? 'ISP估计' : n.residential === 'datacenter' ? '机房' : '属性未知'}`,
    type: 'vless', server: host, port: 443, uuid, udp: false, tls: true,
    servername: host, network: 'ws', 'client-fingerprint': 'chrome', 'skip-cert-verify': false,
    'ws-opts': {path: `/sstp://vpn:vpn@${n.host}:${n.port}`, headers: {Host: host}},
  }));
}

export function residentialCandidates(nodes) {
  return nodes.filter(n => n.residential === 'residential' && n.exit?.is_datacenter === false &&
    n.exit?.is_abuser !== true && n.exit?.is_tor !== true && typeof n.exit?.ip === 'string' &&
    typeof n.exit?.org === 'string' && n.exit.org.length > 0);
}

export function subscriptionMetadata(data, nodes, proxies, residentialOnly) {
  return {mode: residentialOnly ? 'residential-candidates' : 'mixed',
    generated_at: data.generated_at, expires_at: data.expires_at,
    nodes: Object.fromEntries(proxies.map((p, i) => [p.name, {
      host: nodes[i].host, port: nodes[i].port, exit_ip: nodes[i].exit?.ip,
      candidate: nodes[i].residential === 'residential', org: nodes[i].exit?.org,
      is_datacenter: nodes[i].exit?.is_datacenter, is_abuser: nodes[i].exit?.is_abuser,
    }]))};
}

export function clashConfig(proxies) {
  const names = proxies.map(p => p.name);
  return {
    'mixed-port': 17898, 'allow-lan': false, 'bind-address': '127.0.0.1', mode: 'rule',
    'log-level': 'warning', ipv6: false, tun: {enable: false}, dns: {enable: false},
    proxies, 'proxy-groups': [
      {name: 'GATE', type: 'select', proxies: ['GATE-FALLBACK', ...names]},
      {name: 'GATE-FALLBACK', type: 'fallback', proxies: names, url: 'https://www.gstatic.com/generate_204', interval: 300, lazy: false, timeout: 10000},
    ], rules: ['MATCH,GATE'],
  };
}

export function karingConfig(nodes, host, uuid) {
  const proxies = proxyEntries(nodes, host, uuid);
  for (let i = 0; i < proxies.length; i++) {
    const n = nodes[i];
    proxies[i].name = `${n.exit?.country_code || n.country_code || '??'}-${n.host}:${n.port}-ISP候选`;
  }
  const names = proxies.map(p => p.name);
  return {proxies, 'proxy-groups': [
    {name: '住宅候选', type: 'select', proxies: ['住宅候选-自动选择', ...names]},
    {name: '住宅候选-自动选择', type: 'url-test', proxies: names,
      url: 'https://www.google.com/generate_204', interval: 120, timeout: 15000, tolerance: 200, lazy: false},
  ], rules: ['MATCH,住宅候选']};
}

export function vlessLinks(proxies) {
  return proxies.map(p => {
    const q = new URLSearchParams({encryption: 'none', security: 'tls', type: 'ws', sni: p.servername,
      host: p.servername, fp: 'chrome', path: p['ws-opts'].path});
    return `vless://${p.uuid}@${p.server}:443?${q}#${encodeURIComponent(p.name)}`;
  }).join('\n');
}
