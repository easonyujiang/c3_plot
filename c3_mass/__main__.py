"""Native local-service entry point: python -m c3_mass."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import threading
import webbrowser


def open_workspace(url):
    if os.name == 'posix' and Path('/proc/sys/fs/binfmt_misc/WSLInterop').exists():
        opener = shutil.which('wslview') or '/mnt/c/Windows/explorer.exe'
        if Path(opener).exists():
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return
    webbrowser.open(url)


def main():
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / '.env', override=False)
    parser = argparse.ArgumentParser(description='C3-Mass local rocket workspace')
    parser.add_argument('--host', default=os.environ.get('C3_MASS_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('C3_MASS_PORT', '8765')))
    parser.add_argument('--doctor', action='store_true', help='Check the environment without starting the service')
    parser.add_argument('--open', action='store_true', help='Open the workspace in your browser')
    args = parser.parse_args()
    if args.doctor:
        from .doctor import diagnose
        checks = diagnose(root)
        for check in checks:
            mark = 'OK' if check['ok'] else ('FAIL' if check['required'] else 'INFO')
            print(f"[{mark}] {check['name']}: {check['detail']}")
        return 0 if all(check['ok'] for check in checks if check['required']) else 1
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if not (root / 'frontend/dist/index.html').is_file():
        parser.error('Frontend is missing. Run npm --prefix frontend run build.')
    from server import create_app
    from waitress import create_server
    server = create_server(create_app(), host=args.host, port=args.port, threads=4)
    url = f'http://127.0.0.1:{args.port}'
    print(f'C3-Mass  |  {url}', flush=True)
    if args.open:
        timer = threading.Timer(0.5, open_workspace, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == '__main__':
    raise SystemExit(main())
