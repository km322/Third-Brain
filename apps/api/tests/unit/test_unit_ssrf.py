"""Unit: SSRF protection in remote-URL ingestion.

``_resolve_public_target`` must reject any URL whose host resolves to a non-public
address and must pin an accepted URL to the exact resolved IP (closing the DNS-rebinding
window). IP-literal hosts exercise the address filter without touching the network.
"""

from __future__ import annotations

import pytest

from app.services.extractors import _resolve_public_target


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",  # loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://10.0.0.5/",  # private
        "http://192.168.1.1/",  # private
        "http://100.100.100.200/",  # carrier-grade NAT (100.64.0.0/10)
        "http://[::1]/",  # loopback (IPv6)
        "http://0.0.0.0/",  # unspecified
        "ftp://example.com/file",  # non-http(s) scheme
        "http:///nohost",  # missing host
    ],
)
async def test_rejects_non_public_or_invalid(url: str) -> None:
    with pytest.raises(ValueError):
        await _resolve_public_target(url)


async def test_pins_public_ip_literal_and_preserves_host() -> None:
    target = await _resolve_public_target("https://1.1.1.1:8443/path?q=1")
    # The connection target is the literal IP; the Host header + SNI carry the authority so
    # a virtual host is reached and TLS still validates against the hostname/cert.
    assert target.url == "https://1.1.1.1:8443/path?q=1"
    assert target.host_header == "1.1.1.1:8443"
    assert target.sni_hostname == "1.1.1.1"


async def test_strips_userinfo_from_host_header() -> None:
    target = await _resolve_public_target("http://user:pass@8.8.8.8/x")
    assert target.url == "http://8.8.8.8/x"
    assert target.host_header == "8.8.8.8"
