"""CLI entrypoint for FPS Bot Arena."""

import argparse
import tkinter as tk

from .app import FPSBotArena


def main() -> None:
    parser = argparse.ArgumentParser(description="FPS Bot Arena")
    parser.add_argument("--host", action="store_true", help="Run as co-op host")
    parser.add_argument("--join", metavar="HOST", help="Join co-op host by IP or hostname")
    parser.add_argument("--port", type=int, default=5050, help="Co-op port (default: 5050)")
    parser.add_argument("--name", default="Player", help="Player display name")
    args = parser.parse_args()

    mode = "single"
    connect_host = "127.0.0.1"
    if args.host:
        mode = "host"
    elif args.join:
        mode = "client"
        connect_host = args.join

    root = tk.Tk()
    game = FPSBotArena(root, mode=mode, connect_host=connect_host, port=args.port, player_name=args.name)
    _ = game
    root.mainloop()


if __name__ == "__main__":
    main()
