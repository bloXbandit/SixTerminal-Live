"""
launch.py — Entry point for the Stelic Insights Live executable.

When built with PyInstaller this becomes SteliLive.exe.
Starts the Flask server on port 5100 and opens the browser automatically.
"""
import sys
import os
import threading
import webbrowser
import time


def _banner():
    print("=" * 50)
    print("  Stelic Insights Live")
    print("  http://localhost:5100")
    print("=" * 50)
    print("Server starting — browser will open automatically.")
    print("Close this window to stop the server.\n")


def _open_browser():
    time.sleep(2.5)
    webbrowser.open("http://localhost:5100")


if __name__ == "__main__":
    _banner()

    # Open browser in background so it doesn't block server startup
    threading.Thread(target=_open_browser, daemon=True).start()

    # Import and run Flask app (server.py uses Path(__file__).parent for ROOT,
    # which resolves correctly inside the PyInstaller _MEIPASS temp dir)
    from server import app
    try:
        app.run(host="127.0.0.1", port=5100, debug=False, use_reloader=False)
    except OSError as e:
        if "Address already in use" in str(e) or "10048" in str(e):
            print("\n[!] Port 5100 is already in use.")
            print("    Another instance may be running.")
            print("    Close it and try again, or open http://localhost:5100 directly.")
        else:
            raise
    input("\nPress Enter to exit...")
