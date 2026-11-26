from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts.client_runtime import (
    Failover,
    blocked_config,
    matching_node,
    retry_delay,
    validate_snapshot,
)


def snapshot():
    now = 1800000000
    source = {'proxies': [{'name': 'home', 'type': 'vless', 'server': 'gate.test',
                           'servername': 'gate.test', 'tls': True, 'skip-cert-verify': False,
                           'ws-opts': {'path': '/sstp://vpn:vpn@a.opengw.net:443'}}],
              'x-gate': {'mode': 'residential-candidates',
                         'generated_at': datetime.fromtimestamp(now - 10, timezone.utc).isoformat(),
                         'expires_at': datetime.fromtimestamp(now + 3600, timezone.utc).isoformat(),
                         'nodes': {'home': {'host': 'a.opengw.net', 'port': 443, 'exit_ip': '60.73.52.173',
                                            'candidate': True, 'org': 'Example ISP', 'is_datacenter': False}}}}
    return source, {'edge_host': 'gate.test', 'residential_only': True}, now


def test_only_fresh_classified_residential_candidates_are_accepted():
    source, deployment, now = snapshot()
    assert validate_snapshot(source, deployment, now) is source
    for change in [{'candidate': False}, {'is_datacenter': None}, {'is_datacenter': True},
                   {'is_abuser': True}, {'exit_ip': '127.0.0.1'}]:
        altered = deepcopy(source)
        altered['x-gate']['nodes']['home'].update(change)
        with pytest.raises(ValueError):
            validate_snapshot(altered, deployment, now)
    with pytest.raises(ValueError):
        validate_snapshot(source, deployment, now + 4000)


def test_old_mixed_cache_and_metadata_path_mismatch_cannot_bypass_filter():
    source, deployment, now = snapshot()
    source['x-gate']['mode'] = 'mixed'
    with pytest.raises(ValueError):
        validate_snapshot(source, deployment, now)
    source['x-gate']['mode'] = 'residential-candidates'
    source['proxies'][0]['ws-opts']['path'] = '/sstp://vpn:vpn@other.opengw.net:443'
    with pytest.raises(ValueError):
        validate_snapshot(source, deployment, now)


def test_empty_pool_rejects_instead_of_leaking_to_direct_or_datacenter():
    cfg = blocked_config('secret')
    assert cfg['proxies'] == []
    assert cfg['proxy-groups'] == [{'name': 'GATE', 'type': 'select', 'proxies': ['REJECT']}]
    assert cfg['rules'] == ['MATCH,GATE']
    assert cfg['tun']['enable'] is False


def test_failed_node_cooldown_prevents_switching_back_and_expires():
    selector = Failover(['a', 'b', 'c'])
    selector.failed('a', 100)
    selector.failed('b', 110)
    assert selector.candidates('b', 120) == ['c']
    assert selector.candidates('c', 300) == ['a', 'b']
    assert [retry_delay(i) for i in range(1, 9)] == [15, 30, 60, 120, 240, 300, 300, 300]


def test_refresh_preserves_working_host_and_exit_despite_display_name_change():
    source, _, _ = snapshot()
    fresh = deepcopy(source)
    fresh['x-gate']['nodes']['renamed'] = fresh['x-gate']['nodes'].pop('home')
    assert matching_node(source, fresh, 'home') == 'renamed'
    fresh['x-gate']['nodes']['renamed']['exit_ip'] = '8.8.8.8'
    assert matching_node(source, fresh, 'home') is None
    assert matching_node(None, fresh, 'home') is None


@pytest.mark.parametrize('timestamp', [None, 123, '2026-11-26T12:00:00', 'invalid'])
def test_invalid_freshness_never_makes_a_cached_snapshot_usable(timestamp):
    source, deployment, now = snapshot()
    source['x-gate']['expires_at'] = timestamp
    with pytest.raises(ValueError):
        validate_snapshot(source, deployment, now)
