from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path
import json
from tempfile import TemporaryDirectory
from scrape import BrowserTransport, RequestPlan, ImageExtractor
class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'''<body style="min-height:1800px" oncontextmenu="return false">
        <button onclick="this.remove()">Reject All</button><script>
        document.addEventListener('keydown', e => e.preventDefault());
        setTimeout(() => {let i=document.createElement('img'); i.src='/cover.png'; document.body.append(i)}, 20);
        </script>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args): pass
server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
Thread(target=server.serve_forever, daemon=True).start()
try:
    with TemporaryDirectory() as diagnostic_dir:
        result = BrowserTransport(15, 300, diagnostics=diagnostic_dir, reject_cookies=True, full_page=True).execute(RequestPlan(f'http://127.0.0.1:{server.server_port}/', None, {}))
        assert (Path(diagnostic_dir) / 'page.png').read_bytes().startswith(b'\x89PNG')
        report = json.loads((Path(diagnostic_dir) / 'elements.json').read_text(encoding='utf-8'))
        assert report['elements'][0]['tag'] == 'img'
        assert 'naturalWidth' in report['elements'][0]
        assert report['consent_action'] == 'reject_clicked'
        assert report['full_page'] is True
        png = (Path(diagnostic_dir) / 'page.png').read_bytes()
        assert int.from_bytes(png[20:24], 'big') >= 1800
    parser = ImageExtractor(result['url'])
    parser.feed(result['html'])
    assert len(parser.images()) == 1
    assert parser.images()[0]['url'].endswith('/cover.png')
    print('OK : image JavaScript extraite sur fixture locale avec clic droit et touches bloques.')
finally:
    server.shutdown()
    server.server_close()
