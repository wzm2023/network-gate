import {checkProxy} from './vendor/checker.mjs';
import {authorized, validGateAuthority} from './policy.mjs';

const json = (data, status = 200) => Response.json(data, {status, headers: {'Cache-Control': 'no-store'}});
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health') return json({ok: true, service: 'network-gate-checker', build: '20261126'});
    if (!authorized(request, env.CHECK_TOKEN)) return json({error: 'unauthorized'}, 401);
    if (url.pathname !== '/check' || request.method !== 'GET') return json({error: 'not_found'}, 404);
    const value = url.searchParams.get('sstp') || '';
    if (!validGateAuthority(value)) return json({error: 'only_public_vpngate_sstp_allowed'}, 400);
    const started = Date.now();
    let result = await checkProxy({type: 'sstp', value}, request.cf?.colo ?? null);
    let lookupSource = 'iplocate';
    // A failing metadata provider must not be the only reachability probe.
    if (!result.success && /Target|target response|lookup|JSON/.test(result.error || '')) {
      result = await checkProxy({type: 'sstp', value}, request.cf?.colo ?? null, 'cloudflare');
      lookupSource = 'cloudflare';
    }
    return json({...result, lookupSource, responseTime: Date.now() - started});
  },
};
