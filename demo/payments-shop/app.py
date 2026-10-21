"""A tiny shop used for the HTTP 500 demo. It needs PAYMENT_API_KEY on every request, as a real shop needs its payment provider's key: remove the variable and the
site answers 500 with the reason in its log, which is what the assistant is then asked to find. Standard library only."""
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ITEMS = [("Notebook", 120_000), ("Backpack", 890_000), ("Desk lamp", 450_000)]


def payment_key() -> str:
    key = os.getenv("PAYMENT_API_KEY")
    if not key:
        raise RuntimeError("PAYMENT_API_KEY is not set")
    return key


class Shop(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if self.path == "/healthz":
                return self.reply(200, "ok", "text/plain")
            key = payment_key()          # the shop cannot render prices without its payment provider
            rows = "".join(f"<li>{name}: {price:,} toman</li>" for name, price in ITEMS)
            self.reply(200, f"<h1>Payments shop</h1><ul>{rows}</ul><p>Payments are on (key ends {key[-2:]}).</p>")
        except Exception:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            self.reply(500, "<h1>Something went wrong</h1>")

    def reply(self, status, body, kind="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    print(f"Payments shop listening on 0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Shop).serve_forever()
