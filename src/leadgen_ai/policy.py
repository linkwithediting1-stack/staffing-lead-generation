from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if not host:
        return value.strip()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        query.append((key, item))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query), ""))


def _domain_matches(domain: str, configured: str) -> bool:
    configured = normalize_domain(configured)
    return domain == configured or domain.endswith(f".{configured}")


@dataclass(slots=True)
class UrlPolicy:
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)

    def validate(self, url: str) -> str:
        canonical = canonicalize_url(url)
        parts = urlsplit(canonical)
        if parts.scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported URL scheme: {parts.scheme or 'missing'}")
        if parts.username or parts.password:
            raise ValueError("Credentials must not be embedded in URLs")
        domain = normalize_domain(parts.hostname or "")
        if not domain:
            raise ValueError("URL has no hostname")
        if any(_domain_matches(domain, item) for item in self.denied_domains):
            raise ValueError(f"Domain is denied by configuration: {domain}")
        if self.allowed_domains and not any(_domain_matches(domain, item) for item in self.allowed_domains):
            raise ValueError(f"Domain is outside crawler.allowed_domains: {domain}")
        _reject_non_public_ip_literal(domain)
        return canonical

    def assert_public_dns(self, url: str) -> None:
        hostname = urlsplit(url).hostname or ""
        try:
            addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve hostname {hostname}: {exc}") from exc
        for item in addresses:
            address = item[4][0]
            ip = ipaddress.ip_address(address.split("%", 1)[0])
            if not ip.is_global:
                raise ValueError(f"Refusing non-public address for {hostname}: {ip}")


def _reject_non_public_ip_literal(hostname: str) -> None:
    try:
        ip = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        return
    if not ip.is_global:
        raise ValueError(f"Refusing non-public address: {ip}")

