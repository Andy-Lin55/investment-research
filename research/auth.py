"""Explicit personal OneDrive device authorization; tokens never go to stdout."""
import argparse,json,os,re,time
from pathlib import Path
import sys

from research.cli import secure_token_file
from research.delivery import ServiceError


def authorize(client_id,post,show,persist,*,sleep=time.sleep):
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',client_id or ''):
        raise ValueError('your registered personal-account application client ID is required')
    if not all(callable(x) for x in (post,show,persist)):raise ValueError('authorization callbacks required')
    scopes='offline_access Files.ReadWrite User.Read'
    start=post('/consumers/oauth2/v2.0/devicecode',{'client_id':client_id,'scope':scopes})
    show({'verification_uri':start['verification_uri'],'user_code':start['user_code'],
          'expires_in':start['expires_in']})
    interval=max(1,start.get('interval',5));deadline=time.monotonic()+start['expires_in']
    while time.monotonic()<deadline:
        sleep(min(interval,60))
        result=post('/consumers/oauth2/v2.0/token',{'grant_type':'urn:ietf:params:oauth:grant-type:device_code',
                     'client_id':client_id,'device_code':start['device_code']})
        if result.get('refresh_token'):
            persist(result['refresh_token']);return {'authorized':True}
        if result.get('error')=='slow_down':interval+=5
        elif result.get('error')!='authorization_pending':raise ServiceError(401)
    raise ServiceError(408)


def post(path,body):
    import requests
    try:r=requests.post('https://login.microsoftonline.com'+path,data=body,timeout=(15,45))
    except requests.RequestException:raise ServiceError(0) from None
    result=r.json()
    if r.status_code not in {200,400}:raise ServiceError(r.status_code)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-id',required=True);parser.add_argument('--cache',required=True,type=Path)
    args=parser.parse_args(argv)
    if os.getenv('CI','').lower()=='true' or os.getenv('GITHUB_ACTIONS')=='true':
        print('Interactive authorization is local only',file=sys.stderr);return 2
    try:
        authorize(args.client_id,post,lambda x:print(json.dumps(x),flush=True),lambda x:secure_token_file(args.cache,x))
        print('{"authorized":true,"tokens_printed":false}');return 0
    except Exception as error:
        print('Authorization incomplete: '+type(error).__name__,file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
