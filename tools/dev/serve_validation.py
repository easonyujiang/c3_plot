"""Serve an empty disposable workspace; game samples are read from KSP in place."""
import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import tempfile

from tools.paths import ROOT


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional retained output under project output/; default is temporary')
    parser.add_argument('--port', type=int, default=8878)
    args = parser.parse_args()
    with ExitStack() as stack:
        if args.output:
            output = args.output.resolve()
            if not output.is_relative_to(ROOT / 'output'):
                parser.error('Validation output must be under project output/')
            output.mkdir(parents=True, exist_ok=True)
        else:
            output = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='c3-ui-service-')))
        db = output / 'validation.db'
        if db.exists():
            parser.error('Choose a fresh validation directory')
        os.environ['C3_MASS_DB_PATH'] = str(db)
        os.environ['C3_MASS_ROCKETS_DIR'] = str(output / 'rockets')
        os.environ['C3_MASS_OUTPUT_DIR'] = str(output / 'generated')
        from server import create_app
        from waitress import create_server
        server = create_server(create_app(), host='127.0.0.1', port=args.port, threads=4)
        def stop(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        print(f'Empty validation workspace: http://127.0.0.1:{args.port}', flush=True)
        try:
            server.run()
        except KeyboardInterrupt:
            pass
        finally:
            server.close()


if __name__ == '__main__':
    main()
