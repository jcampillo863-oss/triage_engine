from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


HOST = "127.0.0.1"
PORT = 8091


class PayPalCallbackHandler(BaseHTTPRequestHandler):

    def _send_page(self, title: str, message: str):
        body = f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
</head>
<body style="font-family: sans-serif; max-width: 700px; margin: 60px auto;">
    <h1>{title}</h1>
    <p>{message}</p>
    <p>You may return to PowerShell.</p>
</body>
</html>
"""

        encoded = body.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/paypal/return":
            token = params.get("token", [""])[0]

            print()
            print("=== PAYPAL SANDBOX RETURN ===")
            print("BUYER APPROVAL RETURNED")
            print("ORDER TOKEN PRESENT:", bool(token))
            print("=============================")
            print()

            self._send_page(
                "PayPal Sandbox Approval Returned",
                "The sandbox buyer returned successfully after approval."
            )
            return

        if parsed.path == "/paypal/cancel":
            print()
            print("=== PAYPAL SANDBOX CANCEL ===")
            print("BUYER CANCELLED")
            print("=============================")
            print()

            self._send_page(
                "PayPal Sandbox Cancelled",
                "The sandbox buyer cancelled the transaction."
            )
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # Keep test output clean.
        return


def main():
    server = HTTPServer((HOST, PORT), PayPalCallbackHandler)

    print("PayPal sandbox callback running.")
    print(f"RETURN: http://{HOST}:{PORT}/paypal/return")
    print(f"CANCEL: http://{HOST}:{PORT}/paypal/cancel")
    print()
    print("Leave this PowerShell window open.")
    print("Press Ctrl+C when testing is finished.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Stopping sandbox callback.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()