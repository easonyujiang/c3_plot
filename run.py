"""
C3-Mass Performance Dashboard — Entry Point
============================================
Usage:  python run.py [port]
Opens browser with full rocket management UI and query tools.

Backed by SQLite database with Flask API server.
"""
import sys
import webbrowser
import threading

from server import create_app
from server.config import PORT as DEFAULT_PORT, HOST

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    app = create_app()
    print(f"\n  C3-Mass Dashboard")
    print(f"  Dashboard: http://{HOST}:{port}\n")

    def open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open(f'http://{HOST}:{port}')

    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=HOST, port=port, debug=False)


if __name__ == '__main__':
    main()
