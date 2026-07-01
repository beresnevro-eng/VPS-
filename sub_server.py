#!/usr/bin/env python3
"""HTTP/HTTPS сервер подписки для V2RayTun / Happ (base64 список vless://)."""
from __future__ import annotations

import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clients import build_subscription_payload, ensure_meta, load_db, save_db

TLS_CERT = Path("/etc/hysteria/server.crt")
TLS_KEY = Path("/etc/hysteria/server.key")
LE_CERT = Path("/etc/letsencrypt/live/287289.fornex.cloud/fullchain.pem")
LE_KEY = Path("/etc/letsencrypt/live/287289.fornex.cloud/privkey.pem")


def _tls_paths() -> tuple[Path, Path]:
    if LE_CERT.is_file() and LE_KEY.is_file():
        return LE_CERT, LE_KEY
    return TLS_CERT, TLS_KEY


def _make_handler(token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            expected = f"/sub/{token}"
            if path != expected and not path.startswith(expected + "/"):
                self.send_error(404)
                return
            try:
                payload = build_subscription_payload()
            except Exception as e:
                self.send_error(500, str(e)[:100])
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", "inline; filename=subscription.txt")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Profile-Update-Interval", "12")
            self.send_header("Subscription-Userinfo", "upload=0; download=0; total=0; expire=0")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args) -> None:
            pass

    return Handler


def _serve(port: int, handler: type[BaseHTTPRequestHandler], use_tls: bool) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    if use_tls:
        cert, key = _tls_paths()
        if not cert.is_file() or not key.is_file():
            print(f"[!] sub_server TLS: нет {cert}", flush=True)
            return
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        print(f"[+] Subscription HTTPS :{port}", flush=True)
    else:
        print(f"[+] Subscription HTTP :{port}", flush=True)
    srv.serve_forever()


def start_subscription_server() -> None:
    db = load_db()
    meta = ensure_meta(db)
    save_db(db)
    token = meta["sub_token"]
    http_port = int(meta.get("sub_port", 2097))
    tls_port = int(meta.get("sub_tls_port", 2098))
    handler = _make_handler(token)

    threading.Thread(
        target=_serve, args=(http_port, handler, False), name="sub_http", daemon=True
    ).start()

    cert, key = _tls_paths()
    if cert.is_file() and key.is_file():
        threading.Thread(
            target=_serve, args=(tls_port, handler, True), name="sub_https", daemon=True
        ).start()
