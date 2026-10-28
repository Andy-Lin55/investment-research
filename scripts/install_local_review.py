"""Prepare a macOS subscription worker LaunchAgent; installation is explicit."""
import argparse,json,os,plistlib,subprocess,sys
from pathlib import Path

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True);p.add_argument('--write',action='store_true')
    a=p.parse_args(argv)
    root=Path(__file__).resolve().parent.parent
    target=Path.home()/'Library/LaunchAgents/com.investment-research.subscription.plist'
    if not a.write:print(json.dumps({'written':False,'target':str(target)}));return 0
    if sys.platform!='darwin' or not a.env_file.is_file():raise ValueError('macOS and an existing private configuration required')
    logs=Path.home()/'.config/investment-research/logs';logs.mkdir(parents=True,exist_ok=True,mode=0o700)
    target.parent.mkdir(parents=True,exist_ok=True)
    job=dict(Label='com.investment-research.subscription',ProgramArguments=[sys.executable,'-m','research.cli','local','--write',
             '--env-file',str(a.env_file.resolve())],WorkingDirectory=str(root),RunAtLoad=True,StartInterval=3600,
             StandardOutPath=str(logs/'subscription.out.log'),StandardErrorPath=str(logs/'subscription.err.log'))
    with target.open('xb') as stream:plistlib.dump(job,stream)
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(target)],check=True,capture_output=True)
    print(json.dumps({'installed':True,'target':str(target)}));return 0

if __name__=='__main__':raise SystemExit(main())
