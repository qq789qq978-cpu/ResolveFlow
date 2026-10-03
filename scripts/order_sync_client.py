"""Synchronize one synthetic order as a local workspace administrator, without printing credentials."""
import argparse
import getpass
import json
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',default='http://127.0.0.1:8054')
    p.add_argument('--username',required=True)
    p.add_argument('--order-id')
    p.add_argument('--history',action='store_true')
    args=p.parse_args();u=urllib.parse.urlsplit(args.url)
    if u.scheme!='http' or u.hostname not in ('127.0.0.1','localhost','::1') or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        p.error('Use a loopback HTTP origin without credentials')
    if bool(args.order_id)==args.history:p.error('Choose --order-id or --history')
    def request(path,body=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(args.url.rstrip('/')+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=15) as r:return json.load(r)
    token=None
    try:
        password=getpass.getpass('Personal account password: ')
        token=request('/api/session',{'username':args.username,'password':password})['token']
        del password
        result=request('/api/order-sync',None if args.history else {'order_id':args.order_id},token)
        print(json.dumps(result,ensure_ascii=True));return 0
    except urllib.error.HTTPError as error:
        print(json.dumps({'passed':False,'status':error.code}));return 1
    except Exception as error:
        print(json.dumps({'passed':False,'error_type':type(error).__name__}));return 1
    finally:
        if token:
            try:request('/api/logout',{},token)
            except Exception:pass


if __name__=='__main__':raise SystemExit(main())
