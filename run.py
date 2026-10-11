"""
C3-Mass Performance Dashboard — Entry Point
============================================
Usage:  python run.py [port]
Opens browser with full rocket management UI and query tools.

Backed by SQLite database with Flask API server.
"""
import sys
import os
import webbrowser
import threading

# Ensure we're in the project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from server import create_app
from server.config import PORT as DEFAULT_PORT, HOST

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

app = create_app()

if __name__ == '__main__':
    print(f"\n  C3-Mass Dashboard")
    print(f"  Dashboard: http://{HOST}:{PORT}\n")

    def open_browser():
        import time
        time.sleep(0.5)
        webbrowser.open(f'http://{HOST}:{PORT}')

    threading.Thread(target=open_browser, daemon=True).start()

    app.run(host=HOST, port=PORT, debug=False)
