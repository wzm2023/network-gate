from scripts.isolated_client import isolate_config


def test_subscription_cannot_take_over_system_or_existing_ports():
    config = isolate_config({
        'proxies': [{'name': 'a'}], 'proxy-groups': [],
        'mixed-port': 7897, 'port': 7890, 'redir-port': 1234, 'tproxy-port': 1235,
        'allow-lan': True, 'bind-address': '0.0.0.0',
        'external-controller': '0.0.0.0:9090', 'external-controller-pipe': 'existing',
        'tun': {'enable': True, 'auto-route': True}, 'dns': {'enable': True, 'listen': ':53'},
        'listeners': [{'port': 80}], 'rules': ['MATCH,DIRECT'],
        'profile': {'store-selected': True, 'store-fake-ip': True},
    }, 'x' * 40)
    assert config['mixed-port'] == 17898
    assert config['external-controller'] == '127.0.0.1:19091'
    assert config['allow-lan'] is False
    assert config['tun'] == {'enable': False}
    assert config['dns'] == {'enable': False}
    assert config['rules'] == ['MATCH,GATE']
    assert config['profile'] == {'store-selected': False, 'store-fake-ip': False}
    for field in ['port', 'redir-port', 'tproxy-port', 'listeners', 'external-controller-pipe']:
        assert field not in config
