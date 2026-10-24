"""Run the isolated Vite development UI with the local Python API."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[1]
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise SystemExit('Node.js and npm are required for frontend development.')
    from dotenv import load_dotenv
    load_dotenv(root / '.env', override=False)
    children = []
    try:
        children.append(subprocess.Popen([sys.executable, '-m', 'c3_mass'], cwd=root))
        children.append(subprocess.Popen([npm, '--prefix', 'frontend', 'run', 'dev'], cwd=root))
        while all(child.poll() is None for child in children):
            time.sleep(0.3)
        return next((child.returncode for child in children if child.returncode is not None), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
