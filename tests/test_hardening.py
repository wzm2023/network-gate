import asyncio
import time

import httpx
import vpngate
from test_vpngate import make_config


def node(name="test"):
    return vpngate.Node(f"{name}.opengw.net", 443, "8.8.8.8", "Japan", "JP", 10, 1)


def test_unknown_is_not_residential():
    assert vpngate.classify_network("", False) == "unknown"
    assert vpngate.classify_network("Some organization", False) == "unknown"
    assert vpngate.classify_network("NTT EAST", None) == "unknown"
    assert vpngate.classify_network("NTT EAST", False) == "residential"


def test_flags_and_malformed_success_are_not_coerced():
    result = vpngate.parse_check_payload(node(), {"success": "false"})
    assert result["success"] is False
    result = vpngate.parse_check_payload(node(), {
        "success": True, "responseTime": 50,
        "exit": {"ip": "8.8.8.8", "asn": {"name": "NTT EAST"}, "is_vpn": True, "is_abuser": True},
    })
    assert result["residential"] == "unknown"
    assert result["exit"]["is_datacenter"] is None
    assert result["exit"]["is_vpn"] is True
    assert vpngate.exclusion_reason(result, make_config()) == "abuse_flag"


def test_overall_deadline_cancels_io_and_does_not_start_entire_queue(monkeypatch):
    seen = []
    cancelled = []
    async def slow(request):
        seen.append(request)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(request)
            raise
    real_client = httpx.AsyncClient
    monkeypatch.setattr(vpngate.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(slow), **kwargs))
    start = time.monotonic()
    results, timed_out = vpngate.check_all([node(str(i)) for i in range(12)], None,
                                         make_config(deadline_s=0.15, concurrency=2))
    assert time.monotonic() - start < 1.5
    assert len(seen) == len(cancelled) == 2
    assert timed_out == len(results) == 12
    assert all(r["error"] == "overall_deadline" for r in results)


def test_check_auth_header_and_no_redirect(monkeypatch):
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://outside.invalid/check"})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(vpngate.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    results, _ = vpngate.check_all([node()], None, make_config(worker_token="x" * 40))
    assert seen[0].headers["Authorization"] == "Bearer " + "x" * 40
    assert len(seen) == 1
    assert results[0]["worker_error"] is True


def test_latency_filter_rejects_nan_and_missing():
    for latency in (None, float("nan"), -1, True):
        assert vpngate.exclusion_reason({"success": True, "exit": {"ip": "8.8.8.8"}, "latency_ms": latency}, make_config()) == "invalid_latency"


def test_country_grouping_uses_measured_exit():
    item = {**node().to_dict(), "success": True, "exit": {"country_code": "US"}, "residential": "unknown"}
    data = vpngate.build_outputs([item], 1, 1, "test", "test", 1, 0)
    assert "US" in data["countries"]
    assert "JP" not in data["countries"]
