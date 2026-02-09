"""Core dataclasses for game state and UI."""

import socket
from dataclasses import dataclass, field

from .config import WEAPON_DATA, WEAPON_ORDER


@dataclass
class Bot:
    x: float
    y: float
    health: float
    speed: float
    kind: str = "grunt"
    fire_cooldown: float = 0.0
    ai_cooldown: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    state: str = "advance"
    alive: bool = True
    radius: float = 0.28
    attack_range: float = 11.5
    hit_bonus: float = 0.0
    damage_min: int = 4
    damage_max: int = 9
    money_multiplier: float = 1.0


@dataclass
class MoneyDrop:
    x: float
    y: float
    value: int
    ttl: float = 24.0


@dataclass
class RemotePlayer:
    player_id: str
    name: str
    x: float
    y: float
    angle: float
    health: float = 100.0
    money: int = 0
    current_weapon: str = "pistol"
    owned_weapons: dict[str, bool] = field(default_factory=lambda: make_owned_weapons())
    ammo: dict[str, int] = field(default_factory=lambda: make_ammo())
    clip: dict[str, int] = field(default_factory=lambda: make_clip())
    next_fire_at: float = 0.0
    time_since_damage: float = 0.0
    keys: set[str] = field(default_factory=set)
    shooting: bool = False
    downed: bool = False
    bleed_out: float = 0.0
    revive_progress: float = 0.0
    kills: int = 0
    deaths: int = 0
    headshots: int = 0


@dataclass
class TeammateView:
    player_id: str
    name: str
    x: float
    y: float
    angle: float
    health: float
    weapon: str
    downed: bool = False
    money: int = 0
    kills: int = 0
    deaths: int = 0
    headshots: int = 0


@dataclass
class PauseHitbox:
    x1: float
    y1: float
    x2: float
    y2: float
    action: str

    def contains(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2


@dataclass
class _NetConn:
    sock: socket.socket
    buffer: str = ""


def make_owned_weapons() -> dict[str, bool]:
    return {name: (name == "pistol") for name in WEAPON_ORDER}


def make_ammo() -> dict[str, int]:
    return {
        "pistol": WEAPON_DATA["pistol"]["ammo_pack"],
        "shotgun": 0,
        "rifle": 0,
        "rpg": 0,
    }


def make_clip() -> dict[str, int]:
    return {weapon: int(WEAPON_DATA[weapon]["mag_size"]) for weapon in WEAPON_ORDER}
