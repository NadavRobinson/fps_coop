# FPS Bot Arena (Desktop App)

Prototype FPS arena app built as a native desktop Python app (`tkinter`, no browser).

## Run

```powershell
python fps_bot_arena.py
```

## Project Structure

- `fps_bot_arena.py`: compatibility launcher
- `fps_arena/main.py`: CLI entrypoint + startup
- `fps_arena/app.py`: main game app class and loop
- `fps_arena/network.py`: host/client networking
- `fps_arena/models.py`: dataclasses and shared state models
- `fps_arena/config.py`: constants and static game data
- `fps_arena/utils.py`: math/render helpers

## Controls

- `W/A/S/D`: move
- `Mouse`: look (cursor is hidden + locked to game window while playing)
- `Left Click` (hold): fire
- `Middle/Right Click` or `Q`: team ping marker
- `B`: toggle real-time shop/inventory wheel (also unlocks/locks cursor)
- `1/2/3/4`: quick buy or switch weapons
- `R`: reload while alive
- `F1/F2/F3/F4`: spend perk points (vitality/mobility/regen/weapon)
- `Esc`: open/close pause + settings menu (mouse sensitivity, fullscreen, resolution, FOV, FPS cap)
- `R`: restart after death or glitch ending (when dead/bsod)

Settings are persisted to `~/.fps_bot_arena_settings.json`.
Progress/perks are persisted to `~/.fps_bot_arena_profile.json`.
Mouse smoothing is enabled by default and can be toggled in the ESC menu.

## Gameplay Loop

- Start with pistol and survive waves of tactical bots and objective waves.
- Bot archetypes include flankers, tanks, sharpshooters, and periodic bosses.
- Health regenerates slowly after 4 seconds without taking damage (boostable via perks).
- Bots drop physical money pickups (split or shared money mode in co-op).
- Buy stronger weapons via the shop wheel, reload magazines, and land headshots for bonus damage.
- Earn XP, level up, spend perk points, and unlock attachment tiers.
- Buying and firing the RPG triggers the fake crash ending.


## Co-op Multiplayer (Windows + macOS)

Use LAN/WAN TCP host-join mode.

- Host: `python fps_bot_arena.py --host --port 5050 --name HostName`
- Join: `python fps_bot_arena.py --join HOST_IP --port 5050 --name PlayerName`

Notes:

- Host simulates the shared world, bots, drops, and waves.
- Teammates render as blue human players, not bots.
- Open/forward the selected TCP port (`5050` by default) when playing outside LAN.


### Troubleshooting join timeout

- Make sure host is running with `--host` before joining.
- Use host LAN IP (same Wi-Fi): `192.168.x.x` style address.
- On host Windows firewall, allow inbound TCP on your game port (`5050` by default).
- From Mac, test reachability: `nc -vz HOST_IP 5050`.
