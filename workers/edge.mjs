import upstream from './vendor/edgetunnel.mjs';
import {authorized, validGateAuthority, freshNodes, proxyEntries, clashConfig, vlessLinks, residentialCandidates, subscriptionMetadata} from './policy.mjs';

let cachedData = null;
let cachedAt = 0;
async function loadData(env) {
  if (cachedData && Date.now() - cachedAt < 60000) {
    try { freshNodes(cachedData); return cachedData; } catch { cachedData = null; }
  }
  if (env.KV) {
    const stored = await env.KV.get('gate-nodes.json', 'json');
    if (stored) {
      try {
        freshNodes(stored);
        cachedData = stored;
        cachedAt = Date.now();
        return stored;
      } catch { /* A stale KV snapshot must not prevent trying fresh Pages data. */ }
    }
  }
  const response = await fetch(env.NODE_LIST_URL, {signal: AbortSignal.timeout(10000), headers: {'Accept': 'application/json'}, cf: {cacheTtl: 30}});
  if (!response.ok) throw new Error('node_list_unavailable');
  const data = await response.json();
  freshNodes(data);
  cachedData = data;
  cachedAt = Date.now();
  return data;
}
const json = (data, status = 200) => Response.json(data, {status, headers: {'Cache-Control': 'no-store'}});

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === '/health') return json({ok: true, service: 'network-gate-edge', build: '20261126'});
    if (url.pathname === '/check' || url.pathname === '/discovery') {
      if (!authorized(request, env.CHECK_TOKEN)) return json({error: 'unauthorized'}, 401);
      if (request.method !== 'GET') return json({error: 'method_not_allowed'}, 405);
      if (url.pathname === '/check') return env.CHECKER.fetch(request);
      // Fixed public discovery source; no caller-controlled fetch URL or headers.
      const response = await fetch('http://www.vpngate.net/api/iphone/', {signal: AbortSignal.timeout(15000)});
      if (!response.ok) return json({error: 'discovery_unavailable'}, 503);
      return new Response(response.body, {headers: {'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store'}});
    }
    if (url.pathname === '/publish') {
      if (!authorized(request, env.PUBLISH_TOKEN)) return json({error: 'unauthorized'}, 401);
      if (request.method !== 'POST') return json({error: 'method_not_allowed'}, 405);
      const body = await request.text();
      if (body.length > 1500000) return json({error: 'payload_too_large'}, 413);
      try {
        const data = JSON.parse(body);
        const nodes = freshNodes(data);
        if (nodes.length < 3) return json({error: 'minimum_three_nodes_required'}, 422);
        const stored = await env.KV.get('gate-nodes.json', 'json');
        if (stored && Date.parse(stored.generated_at) > Date.parse(data.generated_at))
          return json({error: 'older_snapshot_rejected'}, 409);
        await env.KV.put('gate-nodes.json', JSON.stringify(data));
        cachedData = data;
        cachedAt = Date.now();
        return json({ok: true, count: nodes.length, generated_at: data.generated_at});
      } catch { return json({error: 'invalid_or_expired_snapshot'}, 422); }
    }
    if (url.pathname === '/status' || url.pathname.startsWith('/subscription/')) {
      if (!authorized(request, env.SUB_TOKEN, true)) return json({error: 'unauthorized'}, 401);
      try {
        const data = await loadData(env);
        const all = freshNodes(data);
        const residentialOnly = env.RESIDENTIAL_ONLY === 'true';
        const candidates = residentialCandidates(all);
        const nodes = residentialOnly ? candidates : all;
        if (url.pathname === '/status') return json({ok: nodes.length > 0, count: nodes.length,
          total_count: all.length, residential_candidates: candidates.length,
          mode: residentialOnly ? 'residential-candidates' : 'mixed',
          generated_at: data.generated_at, expires_at: data.expires_at});
        if (!nodes.length) throw new Error('no_residential_candidates');
        const proxies = proxyEntries(nodes, env.HOST || url.hostname, env.UUID);
        const headers = {'Cache-Control': 'private, no-store', 'Content-Type': 'text/plain; charset=utf-8', 'profile-update-interval': '1'};
        if (url.pathname === '/subscription/clash') {
          const config = clashConfig(proxies);
          config['x-gate'] = subscriptionMetadata(data, nodes, proxies, residentialOnly);
          return new Response(JSON.stringify(config, null, 2), {headers});
        }
        if (url.pathname === '/subscription/v2ray') return new Response(btoa(vlessLinks(proxies)), {headers});
        return json({error: 'not_found'}, 404);
      } catch (error) {
        return json({ok: false, error: error.message, message: '节点列表不可用或已过期，请检查更新工作流。'}, 503);
      }
    }
    if (request.headers.get('Upgrade')?.toLowerCase() === 'websocket' ||
        (request.method === 'POST' && !url.pathname.startsWith('/admin/') && url.pathname !== '/login')) {
      const path = decodeURIComponent(url.pathname);
      if (!path.startsWith('/sstp://') || !validGateAuthority(path.slice('/sstp://'.length)))
        return json({error: 'explicit_sstp_exit_required'}, 400);
    }
    return upstream.fetch(request, env, ctx);
  },
};
