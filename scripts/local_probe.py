"""Host-side loopback health observer; local JSON events, no outbound notifications."""
import argparse
from datetime import datetime, timezone
import json
import time
import urllib.parse
import urllib.request


def validate_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'http' or p.hostname not in ('127.0.0.1','localhost','::1') or p.username or p.password or p.query or p.fragment:
        raise ValueError('Only a credential-free local HTTP endpoint is supported')
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def observe(url, timeout=3):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200 and json.load(response).get('status') == 'ok'
    except Exception:
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True,type=validate_url)
    p.add_argument('--interval',type=float,default=10)
    p.add_argument('--once',action='store_true')
    args = p.parse_args()
    if not 1 <= args.interval <= 60: p.error('Interval must be 1..60 seconds')
    previous = None
    while True:
        current = observe(args.url)
        if current != previous:
            print(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                  'event':'local_entry_recovered' if current else 'local_entry_unavailable',
                  'available':current,'scope':'same_host_only'}),flush=True)
        if args.once: return 0 if current else 2
        previous = current
        time.sleep(args.interval)


if __name__ == '__main__': raise SystemExit(main())
