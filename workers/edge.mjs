import upstream from './vendor/edgetunnel.mjs';
import {authorized, validGateAuthority, freshNodes, proxyEntries, clashConfig, vlessLinks} from './policy.mjs';

let cachedData = null;
let cachedAt = 0;
async function loadData(env) {
  if (cachedData && Date.now() - cachedAt < 60000) return cachedData;
  if (env.KV) {
    const stored = await env.KV.get('gate-nodes.json', 'json');
    if (stored) {
      freshNodes(stored);
      cachedData = stored;
      cachedAt = Date.now();
      return stored;
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
        const nodes = freshNodes(data);
        if (url.pathname === '/status') return json({ok: true, count: nodes.length, generated_at: data.generated_at, expires_at: data.expires_at});
        const proxies = proxyEntries(nodes, env.HOST || url.hostname, env.UUID);
        const headers = {'Cache-Control': 'private, no-store', 'Content-Type': 'text/plain; charset=utf-8', 'profile-update-interval': '1'};
        if (url.pathname === '/subscription/clash') return new Response(JSON.stringify(clashConfig(proxies), null, 2), {headers});
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
