"""
main.py — Six Terminal Live entry point.

Starts the local Flask server on http://localhost:5100
and opens the browser automatically.

Usage:
    python main.py
    python main.py --port 5200   # use a different port
    python main.py --no-browser  # don't auto-open browser
"""

import sys
import os
import time
import argparse
import threading
import webbrowser

# Ensure project root is on path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def parse_args():
    parser = argparse.ArgumentParser(description="Six Terminal Live — Schedule Editor")
    parser.add_argument("--port", type=int, default=5100, help="Local port (default: 5100)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    return parser.parse_args()


def open_browser(port: int, delay: float = 1.2):
    """Open the browser after a short delay to let the server start."""
    time.sleep(delay)
    webbrowser.open(f"http://localhost:{port}")


def main():
    args = parse_args()

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        print("\n[!] WARNING: No LLM API key found.")
        print("    Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your environment.")
        print("    File loading and editing will work, but natural language interpretation will fail.\n")

    # Import server from project root (avoids importlib subdirectory traversal)
    from server import app

    port = args.port
    url = f"http://localhost:{port}"

    print(f"\n{'='*50}")
    print(f"  Six Terminal Live")
    print(f"  Running at {url}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'='*50}\n")

    if not args.no_browser:
        t = threading.Thread(target=open_browser, args=(port,), daemon=True)
        t.start()

    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
