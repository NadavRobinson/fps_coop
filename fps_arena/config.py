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
        "spread_growth": 0.06,
        "pellets": 1,
        "range": 13.0,
        "ammo_pack": 9999,
        "mag_size": 14,
        "reload_time": 1.0,
        "recoil_scale": 0.55,
        "infinite": True,
    },
    "shotgun": {
        "name": "Shotgun",
        "cost": 320,
        "damage": 12,
        "fire_rate": 0.8,
        "spread": 0.14,
        "spread_growth": 0.12,
        "pellets": 7,
        "range": 8.5,
        "ammo_pack": 36,
        "mag_size": 6,
        "reload_time": 1.85,
        "recoil_scale": 1.25,
        "infinite": False,
    },
    "rifle": {
        "name": "Assault Rifle",
        "cost": 780,
        "damage": 17,
        "fire_rate": 0.11,
        "spread": 0.028,
        "spread_growth": 0.08,
        "pellets": 1,
        "range": 15.0,
        "ammo_pack": 120,
        "mag_size": 30,
        "reload_time": 2.1,
        "recoil_scale": 0.95,
        "infinite": False,
    },
    "rpg": {
        "name": "RPG",
        "cost": 1800,
        "damage": 160,
        "fire_rate": 1.2,
        "spread": 0.01,
        "spread_growth": 0.02,
        "pellets": 1,
        "range": 15.0,
        "ammo_pack": 1,
        "mag_size": 1,
        "reload_time": 2.8,
        "recoil_scale": 1.6,
        "infinite": False,
    },
}

BOT_ARCHETYPES = {
    "grunt": {
        "hp_mult": 1.0,
        "speed_mult": 1.0,
        "attack_range": 11.5,
        "hit_bonus": 0.0,
        "damage_min_bonus": 0,
        "damage_max_bonus": 0,
        "money_mult": 1.0,
    },
    "flanker": {
        "hp_mult": 0.78,
        "speed_mult": 1.35,
        "attack_range": 9.6,
        "hit_bonus": -0.07,
        "damage_min_bonus": -1,
        "damage_max_bonus": 0,
        "money_mult": 1.1,
    },
    "tank": {
        "hp_mult": 1.85,
        "speed_mult": 0.72,
        "attack_range": 10.8,
        "hit_bonus": 0.08,
        "damage_min_bonus": 2,
        "damage_max_bonus": 3,
        "money_mult": 1.45,
    },
    "sharpshooter": {
        "hp_mult": 0.9,
        "speed_mult": 0.95,
        "attack_range": 14.2,
        "hit_bonus": 0.15,
        "damage_min_bonus": 1,
        "damage_max_bonus": 2,
        "money_mult": 1.25,
    },
    "boss": {
        "hp_mult": 3.2,
        "speed_mult": 0.95,
        "attack_range": 13.0,
        "hit_bonus": 0.2,
        "damage_min_bonus": 4,
        "damage_max_bonus": 6,
        "money_mult": 3.2,
    },
}
