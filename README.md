# FPS Bot Arena (Desktop App)

Prototype FPS arena app built as a native desktop Python app (`tkinter`, no browser).

## Run

```powershell
python fps_bot_arena.py
```

## Controls

- `W/A/S/D`: move
- `Mouse`: look (cursor is hidden + locked to game window while playing)
- `Left Click` (hold): fire
- `B`: toggle real-time shop/inventory wheel (also unlocks/locks cursor)
- `1/2/3/4`: quick buy or switch weapons
- `Esc`: quit
- `R`: restart after death or glitch ending

## Gameplay Loop

- Start with pistol and survive waves of tactical bots.
- Health regenerates slowly after 4 seconds without taking damage.
- Bots drop physical money pickups.
- Buy stronger weapons via the shop wheel.
- Buying and firing the RPG triggers the fake crash ending.


## Co-op Multiplayer (Windows + macOS)

Use LAN/WAN TCP host-join mode.

- Host: `python fps_bot_arena.py --host --port 5050 --name HostName`
- Join: `python fps_bot_arena.py --join HOST_IP --port 5050 --name PlayerName`

Notes:

- Host simulates the shared world, bots, drops, and waves.
- Teammates render as blue human players, not bots.
- Open/forward the selected TCP port (`5050` by default) when playing outside LAN.
