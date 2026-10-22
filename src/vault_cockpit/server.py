"""Local web server. Binds to 127.0.0.1 only; every write needs the session token."""
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import actions, store
from .txn import CheckFailed


def _index_html():
    return resources.files("vault_cockpit").joinpath("web/index.html").read_text(encoding="utf-8")


def state(cfg):
    items = store.items(cfg)
    for i in items:
        i["due_now"] = bool(store.is_due(cfg, i))
    return {"config": cfg.public(), "today": store.today(), "items": items, "decisions": store.decisions(cfg)}


ITEM_ACTIONS = {
    "answer": lambda cfg, i, b: actions.answer(cfg, i, b.get("text", ""), b.get("then", "integrate"), b.get("decision"), b.get("due", "")),
    "snooze": lambda cfg, i, b: actions.snooze(cfg, i, b.get("due", "")),
    "reopen": lambda cfg, i, b: actions.reopen(cfg, i),
    "discard": lambda cfg, i, b: actions.discard(cfg, i, b.get("text", "")),
    "duplicate": lambda cfg, i, b: actions.duplicate(cfg, i, b.get("other", "")),
    "owner": lambda cfg, i, b: actions.set_owner(cfg, i, b.get("owner", "")),
}


def make_handler(cfg, token, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "vault-cockpit"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (
                json.dumps(body, ensure_ascii=False).encode() if ctype == "application/json" else body.encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            path = self.path.split("?")[0]
            if path == "/":
                return self._send(200, _index_html().replace("__COCKPIT_TOKEN__", token), "text/html")
            if path == "/api/state":
                return self._send(200, state(cfg))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            if not secrets.compare_digest(self.headers.get("X-Cockpit-Token", ""), token):
                return self._send(403, {"error": "bad token"})
            origin = self.headers.get("Origin")
            if origin and origin.split("//")[-1] not in allowed_hosts:
                return self._send(403, {"error": "bad origin"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                parts = [p for p in self.path.split("?")[0].split("/") if p][1:]  # drop "api"
                result = self._route(parts, body)
                return self._send(200, result)
            except actions.Invalid as e:
                return self._send(400, {"error": str(e)})
            except KeyError as e:
                return self._send(404, {"error": f"not found: {e}"})
            except CheckFailed as e:
                return self._send(409, {"error": "checks failed, nothing was written", "problems": e.problems,
                                        "output": e.output[-4000:]})
            except Exception as e:  # pragma: no cover
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def _route(self, parts, b):
            if parts == ["items"]:
                return actions.create_item(cfg, b.get("title", ""), b.get("text", ""), b.get("domain", ""),
                                           b.get("pages", []), b.get("owner", ""), b.get("decision", ""))
            if len(parts) == 3 and parts[0] == "items" and parts[2] in ITEM_ACTIONS:
                return ITEM_ACTIONS[parts[2]](cfg, parts[1], b)
            if len(parts) == 2 and parts[0] == "integrated":
                return actions.integrated(cfg, parts[1])
            if parts == ["triage"]:
                return actions.triage(cfg, b.get("verdicts", []))
            if parts == ["decisions"]:
                return actions.create_decision(cfg, b.get("title", ""), b.get("subject", ""), b.get("text", ""),
                                               b.get("domain", ""), b.get("decided_by", ""))
            if len(parts) == 3 and parts[0] == "decisions":
                return actions.review_decision(cfg, parts[1], parts[2], b.get("text", ""), b.get("title", ""),
                                               bool(b.get("confirm_triggers")))
            raise KeyError("/".join(parts))

    return Handler


def serve(cfg, port=8765, open_browser=True):
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(cfg, token, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Vault Cockpit for {cfg.vault} on {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
