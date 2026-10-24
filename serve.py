"""Serve the spaceband explorer at http://localhost:8000 and open it in the browser."""
import functools
import http.server
import os
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    # Windows' registry sometimes maps .js to text/plain, which breaks ES modules
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
        ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
    }

    def end_headers(self):
        # always revalidate, so an updated app.js / map.json shows up on a normal refresh
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if not os.path.exists(os.path.join(WEB, "data", "map.json")):
        print("web/data/map.json is missing: run  python pipeline/crawl.py  and  python pipeline/build.py  first.")
    handler = functools.partial(Handler, directory=WEB)
    for port in range(PORT, PORT + 20):  # skip ports already in use (e.g. an older copy still running)
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    url = f"http://localhost:{port}/"
    print(f"spaceband running at {url}  (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
