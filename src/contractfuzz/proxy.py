"""A minimal mutating proxy for JSON responses.

The proxy forwards every request to the upstream server. When a response
matches a path template in the spec, has an ``application/json`` content
type, and its body validates against the contract, the proxy applies one
schema-valid mutation to the *real* upstream body before returning it.
Mutations rotate per route so repeated requests exercise different edge
cases. Two headers document what happened:

- ``X-Contractfuzz-Mutation``: human-readable description of the mutation
- ``X-Contractfuzz-Mutation-Path``: JSONPath-style location of the change

Documented subset (see README): JSON bodies only, buffered (no streaming),
hop-by-hop headers are dropped, upstream bodies that do not validate
against the contract pass through untouched with an explanatory header.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from contractfuzz.generator import Variant
from contractfuzz.mutations import collect_mutations
from contractfuzz.spec import HTTP_METHODS, Schema, resolve_refs, to_json_schema
from contractfuzz.validation import make_validator

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}


def _match_template(template: str, path: str) -> bool:
    t_parts = template.strip("/").split("/")
    p_parts = path.strip("/").split("/")
    if len(t_parts) != len(p_parts):
        return False
    return all(
        t.startswith("{") and t.endswith("}") or t == p
        for t, p in zip(t_parts, p_parts, strict=True)
    )


class RouteTable:
    """Maps (method, concrete path, status) to a resolved response schema."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._routes: dict[tuple[str, str], dict[str, Schema]] = {}
        for template, item in spec.get("paths", {}).items():
            if not isinstance(item, dict):
                continue
            resolved = resolve_refs(item, spec)
            for method in HTTP_METHODS:
                op = resolved.get(method)
                if not isinstance(op, dict):
                    continue
                statuses: dict[str, Schema] = {}
                for status, response in (op.get("responses") or {}).items():
                    if not isinstance(response, dict):
                        continue
                    for media_type, media in (response.get("content") or {}).items():
                        base = media_type.split(";")[0].strip().lower()
                        is_json = base == "application/json" or base.endswith("+json")
                        if is_json and isinstance(media.get("schema"), dict):
                            statuses[str(status)] = to_json_schema(media["schema"])
                            break
                if statuses:
                    self._routes[(method.upper(), template)] = statuses

    def lookup(self, method: str, path: str, status: int) -> tuple[str, Schema] | None:
        for (m, template), statuses in self._routes.items():
            if m != method.upper() or not _match_template(template, path):
                continue
            schema = statuses.get(str(status)) or statuses.get(f"{status // 100}XX")
            if schema is None and status < 400:
                schema = statuses.get("default")
            if schema is not None:
                return template, schema
        return None


class Mutator:
    """Applies rotating schema-valid mutations to upstream JSON bodies."""

    def __init__(self, routes: RouteTable) -> None:
        self._routes = routes
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def mutate(
        self, method: str, path: str, status: int, body: bytes
    ) -> tuple[bytes, dict[str, str]]:
        route = self._routes.lookup(method, path, status)
        if route is None:
            return body, {}
        template, schema = route
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return body, {"X-Contractfuzz": "upstream-body-not-json, passed through"}
        validator = make_validator(schema)
        if not validator.is_valid(data):
            return body, {"X-Contractfuzz": "upstream-body-violates-contract, passed through"}
        variants = [
            Variant(m, mutated)
            for m in collect_mutations(schema, data)
            if validator.is_valid(mutated := m.apply(data))
        ]
        if not variants:
            return body, {"X-Contractfuzz": "no-valid-mutations, passed through"}
        variants.sort(key=lambda v: -v.mutation.danger)
        with self._lock:
            index = self._counters.get(template, 0)
            self._counters[template] = index + 1
        variant = variants[index % len(variants)]
        record = variant.mutation.record()
        headers = {
            "X-Contractfuzz-Mutation": record["description"],
            "X-Contractfuzz-Mutation-Path": record["path"],
        }
        return json.dumps(variant.data).encode("utf-8"), headers


def make_proxy_server(
    spec: dict[str, Any], upstream: str, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """Build (but do not start) the proxy server; port 0 picks a free port."""
    parsed = urlsplit(upstream)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        from contractfuzz.errors import ContractfuzzError

        raise ContractfuzzError(f"--upstream must be an http(s) base URL, got {upstream!r}")
    upstream_base = upstream.rstrip("/")
    mutator = Mutator(RouteTable(spec))

    class ProxyHandler(BaseHTTPRequestHandler):
        server_version = "contractfuzz-proxy"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            pass

        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            request_body = self.rfile.read(length) if length else None
            out_headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
            }
            request = urllib.request.Request(
                upstream_base + self.path,
                data=request_body,
                headers=out_headers,
                method=self.command,
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    status = response.status
                    body = response.read()
                    headers = list(response.headers.items())
            except urllib.error.HTTPError as exc:
                status = exc.code
                body = exc.read()
                headers = list(exc.headers.items())
            except (urllib.error.URLError, OSError) as exc:
                self.send_response(502)
                message = f"contractfuzz proxy: upstream unreachable: {exc}\n".encode()
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return

            content_type = next((v for k, v in headers if k.lower() == "content-type"), "")
            extra: dict[str, str] = {}
            base_type = content_type.split(";")[0].strip().lower()
            if base_type == "application/json" or base_type.endswith("+json"):
                clean_path = self.path.split("?")[0]
                body, extra = mutator.mutate(self.command, clean_path, status, body)

            self.send_response(status)
            for key, value in headers:
                if key.lower() not in _HOP_BY_HOP:
                    self.send_header(key, value)
            for key, value in extra.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    return ThreadingHTTPServer((host, port), ProxyHandler)


def run_proxy(spec: dict[str, Any], upstream: str, host: str, port: int) -> int:
    server = make_proxy_server(spec, upstream, host, port)
    actual_port = server.server_address[1]
    print(f"contractfuzz proxy listening on http://{host}:{actual_port}")
    print(f"forwarding to {upstream}")
    print("JSON responses matching the contract get one rotating schema-valid mutation each.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping proxy")
    finally:
        server.server_close()
    return 0
