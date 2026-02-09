"""Game configuration constants and static data."""

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720
DEFAULT_FOV_DEG = 60
DEFAULT_MOUSE_SENSITIVITY = 0.003
DEFAULT_FPS_LIMIT = 60

MIN_RENDER_WIDTH = 800
MIN_RENDER_HEIGHT = 540
MIN_SENSITIVITY = 0.001
MAX_SENSITIVITY = 0.015
SENSITIVITY_STEP = 0.0003
MIN_FOV_DEG = 50
MAX_FOV_DEG = 110
FOV_STEP_DEG = 5
MIN_FPS_CAP = 30
MAX_FPS_CAP = 240
FPS_STEP = 15

RAY_DENSITY = 5.8
MAX_RAY_COUNT = 300
MAX_DEPTH = 20.0
PLAYER_RADIUS = 0.22
TARGET_FPS = DEFAULT_FPS_LIMIT
HEALTH_REGEN_DELAY = 4.0
HEALTH_REGEN_RATE = 3.0
CONNECT_TIMEOUT_SECONDS = 4.0

WORLD_MAP = [
    "########################",
    "#....#........#........#",
    "#....#........#........#",
    "#....#..####..#..####..#",
    "#....#..#..#..#..#..#..#",
    "#....#..#..#..#..#..#..#",
    "#....####..####..#..#..#",
    "#................#..#..#",
    "####..############..#..#",
    "#........#..........#..#",
    "#........#..######..#..#",
    "#..####..#..#....#..#..#",
    "#..#..#..#..#....#..#..#",
    "#..#..#..#..#....#..#..#",
    "#..#..#..#..######..#..#",
    "#..#..#..#..........#..#",
    "#..#..#..############..#",
    "#..#..#...............##",
    "#........#.............#",
    "########################",
]

WEAPON_ORDER = ["pistol", "shotgun", "rifle", "rpg"]

WEAPON_DATA = {
    "pistol": {
        "name": "Pistol",
        "cost": 0,
        "damage": 24,
        "fire_rate": 0.32,
        "spread": 0.018,
        "pellets": 1,
        "range": 13.0,
        "ammo_pack": 9999,
        "infinite": True,
    },
    "shotgun": {
        "name": "Shotgun",
        "cost": 320,
        "damage": 12,
        "fire_rate": 0.8,
        "spread": 0.14,
        "pellets": 7,
        "range": 8.5,
        "ammo_pack": 36,
        "infinite": False,
    },
    "rifle": {
        "name": "Assault Rifle",
        "cost": 780,
        "damage": 17,
        "fire_rate": 0.11,
        "spread": 0.028,
        "pellets": 1,
        "range": 15.0,
        "ammo_pack": 120,
        "infinite": False,
    },
    "rpg": {
        "name": "RPG",
        "cost": 1800,
        "damage": 160,
        "fire_rate": 1.2,
        "spread": 0.01,
        "pellets": 1,
        "range": 15.0,
        "ammo_pack": 1,
        "infinite": False,
    },
}
