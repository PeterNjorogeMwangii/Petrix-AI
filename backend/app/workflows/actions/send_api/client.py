"""Bounded HTTPS calls; no ambient cookies, redirects, proxies, or retries."""
from dataclasses import dataclass
import ipaddress
import json
import socket
import ssl
import time
from typing import Optional
import zlib

import httpx

from app.workflows.actions.send_api.config import validate_endpoint
from app.workflows.chat_script import ScriptError, json_value

MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class APIResult:
    outcome: str
    status_code: Optional[int] = None
    body: object = None
    error: Optional[str] = None

    def variable(self):
        return {"ok": self.outcome == "success", "status_code": self.status_code, "body": self.body, "error": self.error}


class BlockedEndpoint(ValueError):
    pass


class ResponseLimit(ValueError):
    pass


def resolve_addresses(host, port):
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        return list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


class SendAPIClient:
    def __init__(self, allowed_private_hosts=(), transport_factory=None, resolver=None, ssl_context=None):
        self.allowed_private_hosts = {host.lower().rstrip(".") for host in allowed_private_hosts}
        self.transport_factory = transport_factory
        self.resolver = resolver or resolve_addresses
        self.ssl_context = ssl_context or ssl.create_default_context()

    def pinned_url(self, source):
        validate_endpoint(source)
        url = httpx.URL(source)
        host = url.raw_host.decode("ascii").rstrip(".")
        addresses = self.resolver(host, url.port or 443)
        if not addresses:
            raise socket.gaierror()
        for address in addresses:
            ip = ipaddress.ip_address(address)
            comparable = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            # Metadata/link-local, multicast, and unspecified addresses stay blocked,
            # even when an operator explicitly permits a private API host.
            if comparable.is_link_local or comparable.is_multicast or comparable.is_unspecified or ip.is_reserved:
                raise BlockedEndpoint()
            if not comparable.is_global and host not in self.allowed_private_hosts:
                raise BlockedEndpoint()
        return url.copy_with(host=addresses[0]), host, url.netloc.decode("ascii")

    def send(self, method, url, headers, body, timeout_seconds):
        deadline = time.monotonic() + timeout_seconds
        status = None
        try:
            pinned, host, authority = self.pinned_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return APIResult("timeout", error="timeout")
            transport = self.transport_factory() if self.transport_factory else httpx.HTTPTransport(verify=self.ssl_context, retries=0, trust_env=False)
            values = {"Accept": "application/json", "Accept-Encoding": "identity", **headers, "Host": authority}
            with httpx.Client(transport=transport, timeout=httpx.Timeout(remaining), trust_env=False, follow_redirects=False) as client:
                with client.stream(method, pinned, headers=values, content=body,
                    extensions={"sni_hostname": host}) as response:
                    status = response.status_code
                    size, chunks = 0, []
                    length = response.headers.get("content-length")
                    if length and int(length) > MAX_RESPONSE_BYTES:
                        raise ResponseLimit()
                    for chunk in response.iter_raw():
                        if time.monotonic() >= deadline:
                            return APIResult("timeout", status, error="timeout")
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ResponseLimit()
                        chunks.append(chunk)
                    if time.monotonic() >= deadline:
                        return APIResult("timeout", status, error="timeout")
                    raw = b"".join(chunks)
                    encoding = response.headers.get("content-encoding", "identity").lower().strip()
                    if encoding == "gzip":
                        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                        raw = decoder.decompress(raw, MAX_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_RESPONSE_BYTES or decoder.unconsumed_tail:
                            raise ResponseLimit()
                        if not decoder.eof or decoder.unused_data:
                            return APIResult("error", status, error="invalid_response")
                    elif encoding not in {"", "identity"}:
                        return APIResult("error", status, error="invalid_response")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not raw.strip():
                        value = None
                    elif content_type == "application/json" or content_type.endswith("+json"):
                        try:
                            def invalid_constant(_):
                                raise ValueError()
                            value = json.loads(raw, parse_constant=invalid_constant)
                        except (ValueError, UnicodeError, RecursionError):
                            return APIResult("error", status, error="invalid_response")
                    else:
                        value = httpx.Response(status, content=raw, headers={"content-type": response.headers.get("content-type", "text/plain")}).text
                    result = APIResult("success", status, value) if 200 <= status < 300 else APIResult("error", status, value, "http_error")
                    try:
                        json_value(result.variable())
                    except ScriptError:
                        raise ResponseLimit() from None
                    return result
        except BlockedEndpoint:
            return APIResult("error", error="blocked_endpoint")
        except (httpx.TimeoutException, socket.timeout):
            return APIResult("timeout", status, error="timeout")
        except ResponseLimit:
            return APIResult("error", status, error="response_too_large")
        except (httpx.HTTPError, OSError):
            return APIResult("error", status, error="network_error")
        except (ValueError, TypeError, UnicodeError, zlib.error):
            return APIResult("error", status, error="invalid_response")
