"""Local UI test proxy: delay selected QA responses without changing their body.

Bound to localhost:8005, upstream fixed to isolated demo QA at localhost:8004.
Control JSON: {"id":"unique-scenario", "path":"/api/runs/...", "method":"GET",
               "delay":6, "count":1}. Delays occur AFTER the real response.
Only synthetic QA use; logs exclude credentials and request/response bodies.
"""
import argparse
import http.client
import json
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    counts, lock = {}, threading.Lock()

    def log(**event):
        with lock, args.log.open('a', encoding='utf-8') as output:
            output.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False)+'\n')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            if not self.path.startswith('/') or self.path.startswith('//'):
                self.send_error(400)
                return
            delay, scenario, drop = 0, None, False
            try:
                rule = json.loads(args.control.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                rule = {}
            with lock:
                name = rule.get('id')
                if name and rule.get('path') == self.path and rule.get('method','GET') == self.command and counts.get(name,0) < rule.get('count',1):
                    counts[name] = counts.get(name,0)+1
                    delay, scenario = min(max(float(rule.get('delay',0)),0),8), name
                    drop = bool(rule.get('drop',False))
            upstream = http.client.HTTPConnection('127.0.0.1',8004,timeout=20)
            try:
                body = self.rfile.read(int(self.headers.get('Content-Length','0')))
                headers = {key:self.headers[key] for key in ('Content-Type','X-API-Key') if key in self.headers}
                upstream.request(self.command,self.path,body=body,headers=headers)
                response = upstream.getresponse()
                data, status = response.read(), response.status
                content_type = response.getheader('Content-Type','application/json')
            except OSError:
                data, status, content_type = b'{"detail":"QA upstream unavailable"}', 503, 'application/json'
            finally:
                upstream.close()
            log(event='upstream',path=self.path,method=self.command,status=status,scenario=scenario,delay=delay)
            if delay:
                time.sleep(delay)
            if drop:
                log(event='response-dropped',path=self.path,method=self.command,status=status,scenario=scenario)
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            try:
                self.send_response(status)
                self.send_header('Content-Type',content_type)
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                log(event='delivered',path=self.path,method=self.command,status=status,scenario=scenario)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                log(event='client-disconnected',path=self.path,method=self.command,scenario=scenario)

    server = ThreadingHTTPServer(('127.0.0.1',8005), Handler)
    print('UI QA proxy ready at http://127.0.0.1:8005',flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
