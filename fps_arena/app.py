"""Main game application class and runtime loop."""

import ctypes
import json
import math
import random
import sys
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from pathlib import Path

from .config import (
    BOT_ARCHETYPES,
    DEFAULT_FOV_DEG,
    DEFAULT_FPS_LIMIT,
    DEFAULT_HEIGHT,
    DEFAULT_MOUSE_SENSITIVITY,
    DEFAULT_WIDTH,
    FOV_STEP_DEG,
    FPS_STEP,
    HEALTH_REGEN_DELAY,
    HEALTH_REGEN_RATE,
    MAX_RAY_COUNT,
    MAX_DEPTH,
    MAX_FOV_DEG,
    MAX_FPS_CAP,
    MAX_SENSITIVITY,
    MIN_FOV_DEG,
    MIN_FPS_CAP,
    MIN_RENDER_HEIGHT,
    MIN_RENDER_WIDTH,
    MIN_SENSITIVITY,
    PLAYER_RADIUS,
    RAY_DENSITY,
    SENSITIVITY_STEP,
    WEAPON_DATA,
    WEAPON_ORDER,
    WORLD_MAP,
)
from .models import Bot, MoneyDrop, PauseHitbox, RemotePlayer, TeammateView, make_ammo, make_owned_weapons
from .network import CoopClient, CoopHostServer
from .utils import clamp, distance, normalize_angle, rgb

HAS_WIN32 = hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32")
HAS_MACOS = sys.platform == "darwin"
DEFAULT_MOUSE_SMOOTHING_ENABLED = True
DEFAULT_MOUSE_SMOOTHING_STRENGTH = 0.72
MOUSE_WARP_EDGE_MARGIN = 140
MOUSE_WARP_INTERVAL_SECONDS = 1.0 / 90.0

SETTINGS_FILE_PATH = Path.home() / ".fps_bot_arena_settings.json"
PROFILE_FILE_PATH = Path.home() / ".fps_bot_arena_profile.json"

HAS_MACOS_CURSOR_WARP = False
_macos_app_services = None

if HAS_MACOS:
    class _CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    try:
        _macos_app_services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        _macos_app_services.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
        _macos_app_services.CGWarpMouseCursorPosition.restype = ctypes.c_int
        HAS_MACOS_CURSOR_WARP = True
    except OSError:
        HAS_MACOS_CURSOR_WARP = False

WIDTH = DEFAULT_WIDTH
HEIGHT = DEFAULT_HEIGHT
HALF_HEIGHT = HEIGHT // 2
FOV = math.radians(DEFAULT_FOV_DEG)
RAY_COUNT = max(160, min(MAX_RAY_COUNT, int(WIDTH / RAY_DENSITY)))

RECOIL_PATTERNS = {
    "pistol": [0.0, 0.004, -0.003, 0.005, -0.004, 0.003],
    "shotgun": [0.0, -0.008, 0.007, -0.005],
    "rifle": [0.0, 0.003, -0.002, 0.004, -0.003, 0.002, 0.003, -0.002],
    "rpg": [0.0],
}

class FPSBotArena:
    def __init__(
        self,
        root: tk.Tk,
        mode: str = "single",
        connect_host: str = "127.0.0.1",
        port: int = 5050,
        player_name: str = "Player",
    ) -> None:
        self.root = root
        self.net_mode = mode
        self.player_name = player_name
        self.player_id = "host" if mode == "host" else ""
        self.coop_server: CoopHostServer | None = None
        self.coop_client: CoopClient | None = None
        self.remote_players: dict[str, RemotePlayer] = {}
        self.remote_render_players: list[TeammateView] = []
        self.client_connected = mode != "client"
        self.last_net_send = 0.0
        self.net_send_interval = 1.0 / 30.0
        self.net_status = ""
        self._init_network(mode, connect_host, port, player_name)
        self._configure_window(self._build_window_title(mode))
        self._init_input_and_settings_state()

        self.last_time = time.perf_counter()
        self.damage_flash = 0.0
        self.muzzle_flash_timer = 0.0
        self.weapon_kick = 0.0

        self._build_floor_cells()
        self._build_cover_points()
        self.reset_game()
        self._bind_events()

        self.set_mouse_capture(True)
        self.loop()

    def _init_network(self, mode: str, connect_host: str, port: int, player_name: str) -> None:
        if mode == "host":
            self.coop_server = CoopHostServer("0.0.0.0", port)
            self.net_status = f"Hosting co-op on port {port}"
            return

        if mode != "client":
            return

        try:
            self.coop_client = CoopClient(connect_host, port, player_name)
            self.net_status = f"Joining {connect_host}:{port}"
        except OSError as exc:
            self.coop_client = None
            code = getattr(exc, "winerror", None)
            if code is None:
                code = getattr(exc, "errno", "unknown")
            self.net_status = (
                f"Connection failed ({code}): {exc}. "
                "Check host IP/port and firewall."
            )
            self.client_connected = False

    def _build_window_title(self, mode: str) -> str:
        title = "FPS Bot Arena"
        if mode == "host":
            title += " [CO-OP HOST]"
        elif mode == "client":
            title += " [CO-OP CLIENT]"
        return title

    def _configure_window(self, title: str) -> None:
        self.root.title(title)
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.configure(bg="#111")
        self.root.resizable(False, False)
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg="#101012", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

    def _init_input_and_settings_state(self) -> None:
        self.settings_path = SETTINGS_FILE_PATH
        self.profile_path = PROFILE_FILE_PATH
        self.keys: set[str] = set()
        self.mouse_down = False
        self.last_mouse_x = WIDTH // 2
        self.last_mouse_y = HALF_HEIGHT

        self.mouse_sensitivity = DEFAULT_MOUSE_SENSITIVITY
        self.fov_degrees = DEFAULT_FOV_DEG
        self.fps_limit = DEFAULT_FPS_LIMIT
        self.fullscreen_enabled = False
        self.available_resolutions = self.build_resolution_options()
        self.resolution_index = self.find_resolution_index(WIDTH, HEIGHT)
        self.pause_open = False
        self.pause_hitboxes: list[PauseHitbox] = []
        self.mouse_smoothing_enabled = DEFAULT_MOUSE_SMOOTHING_ENABLED
        self.mouse_smoothing_strength = DEFAULT_MOUSE_SMOOTHING_STRENGTH
        self.smoothed_mouse_dx = 0.0
        self.use_warp_mouse = HAS_WIN32 or HAS_MACOS_CURSOR_WARP
        self.next_warp_allowed_at = 0.0
        self.last_damage_from = 0.0
        self.damage_direction_timer = 0.0
        self.team_ping: tuple[float, float, float, str] | None = None
        self.shared_money = False
        self.objective_type = "eliminate"
        self.objective_timer = 0.0
        self.objective_zone: tuple[float, float, float] | None = None
        self.current_reload_weapon: str | None = None
        self.reload_end_at = 0.0
        self.spread_heat = 0.0
        self.recoil_index = {weapon: 0 for weapon in WEAPON_ORDER}
        self.clip = {weapon: int(WEAPON_DATA[weapon]["mag_size"]) for weapon in WEAPON_ORDER}
        self.adaptive_quality_enabled = True
        self.frame_dt_avg = 1.0 / 60.0
        self.last_quality_adjust = 0.0
        self.base_ray_target = RAY_COUNT
        self.profile_level = 1
        self.profile_xp = 0
        self.perk_points = 0
        self.attachment_tier = 0
        self.perks = {"vitality": 0, "mobility": 0, "regen": 0, "weapon": 0}
        self.remote_interp_targets: dict[str, tuple[float, float, float, float, str, bool, str]] = {}
        self.remote_render_map: dict[str, TeammateView] = {}
        self.wave_cleared_award_pending = False

        self.mouse_locked = True
        self.focused = True

        self.load_profile()
        self.load_user_settings()
        self.apply_fov_setting()
        self.apply_display_settings()

    def _bind_events(self) -> None:
        self.root.bind("<KeyPress>", self.on_key_down)
        self.root.bind("<KeyRelease>", self.on_key_up)
        self.root.bind("<Motion>", self.on_mouse_move)
        self.root.bind("<ButtonPress-1>", self.on_mouse_down)
        self.root.bind("<ButtonPress-2>", self.on_mouse_down)
        self.root.bind("<ButtonPress-3>", self.on_mouse_down)
        self.root.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.root.bind("<FocusIn>", self.on_focus_in)
        self.root.bind("<FocusOut>", self.on_focus_out)
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def reset_game(self) -> None:
        self.pause_open = False
        self.player_x = 2.6
        self.player_y = 2.6
        self.player_angle = 0.15
        self.player_health = self.get_max_health_cap()
        self.player_money = 0
        self.player_downed = False
        self.player_bleed_out = 0.0
        self.player_revive_progress = 0.0

        self.owned_weapons = make_owned_weapons()
        self.ammo = make_ammo()
        self.clip = {weapon: 0 for weapon in WEAPON_ORDER}
        self.clip["pistol"] = int(WEAPON_DATA["pistol"]["mag_size"])
        self.current_weapon = "pistol"
        self.next_fire_at = 0.0
        self.current_reload_weapon = None
        self.reload_end_at = 0.0
        self.spread_heat = 0.0
        self.recoil_index = {weapon: 0 for weapon in WEAPON_ORDER}

        self.shop_open = False
        self.wave = 0
        self.wave_timer = 0.0
        self.wave_cleared_award_pending = False
        self.objective_type = "eliminate"
        self.objective_timer = 0.0
        self.objective_zone = None
        self.team_ping = None

        self.bots: list[Bot] = []
        self.money_drops: list[MoneyDrop] = []

        self.game_state = "playing"
        self.glitch_timer = 0.0
        self.bsod_started_at = 0.0
        self.muzzle_flash_timer = 0.0
        self.weapon_kick = 0.0
        self.time_since_damage = 0.0

        self.remote_render_players = []
        self.remote_render_map.clear()
        self.remote_interp_targets.clear()
        if self.net_mode == "host":
            for remote in self.remote_players.values():
                spawn_x, spawn_y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 4.5)
                remote.x = spawn_x
                remote.y = spawn_y
                remote.angle = random.uniform(0.0, math.tau)
                remote.health = 100.0
                remote.money = 0
                remote.current_weapon = "pistol"
                remote.owned_weapons = make_owned_weapons()
                remote.ammo = make_ammo()
                remote.clip = {weapon: 0 for weapon in WEAPON_ORDER}
                remote.clip["pistol"] = int(WEAPON_DATA["pistol"]["mag_size"])
                remote.next_fire_at = 0.0
                remote.time_since_damage = 0.0
                remote.keys.clear()
                remote.shooting = False
                remote.downed = False
                remote.bleed_out = 0.0
                remote.revive_progress = 0.0

        if self.net_mode != "client":
            self.spawn_wave()

        self.set_mouse_capture(True)

    def build_resolution_options(self) -> list[tuple[int, int]]:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        candidates = [
            (1024, 576),
            (1280, 720),
            (1366, 768),
            (1600, 900),
            (1920, 1080),
            (2560, 1440),
            (screen_w, screen_h),
            (WIDTH, HEIGHT),
        ]
        unique: list[tuple[int, int]] = []
        for w, h in candidates:
            if w < MIN_RENDER_WIDTH or h < MIN_RENDER_HEIGHT:
                continue
            if (w, h) not in unique:
                unique.append((w, h))
        return unique

    def find_resolution_index(self, width: int, height: int) -> int:
        for i, item in enumerate(self.available_resolutions):
            if item == (width, height):
                return i
        self.available_resolutions.append((width, height))
        return len(self.available_resolutions) - 1

    def update_render_metrics(self, width: int, height: int) -> None:
        global WIDTH, HEIGHT, HALF_HEIGHT, RAY_COUNT
        width = max(MIN_RENDER_WIDTH, int(width))
        height = max(MIN_RENDER_HEIGHT, int(height))
        WIDTH = width
        HEIGHT = height
        HALF_HEIGHT = HEIGHT // 2
        self.base_ray_target = max(160, min(MAX_RAY_COUNT, int(WIDTH / RAY_DENSITY)))
        RAY_COUNT = self.base_ray_target
        self.canvas.configure(width=WIDTH, height=HEIGHT)
        self.last_mouse_x = min(self.last_mouse_x, WIDTH - 1)
        self.last_mouse_y = min(self.last_mouse_y, HEIGHT - 1)

    def apply_display_settings(self) -> None:
        width, height = self.available_resolutions[self.resolution_index]
        if self.fullscreen_enabled:
            self.root.attributes("-fullscreen", True)
            self.root.update_idletasks()
            self.update_render_metrics(self.root.winfo_width(), self.root.winfo_height())
        else:
            self.root.attributes("-fullscreen", False)
            self.root.geometry(f"{width}x{height}")
            self.root.update_idletasks()
            self.update_render_metrics(width, height)

        if self.mouse_locked and self.focused:
            self.clip_cursor_to_canvas()

    def apply_fov_setting(self) -> None:
        global FOV
        FOV = math.radians(self.fov_degrees)

    def toggle_pause_menu(self) -> None:
        self.pause_open = not self.pause_open
        self.keys.clear()
        self.mouse_down = False
        if self.pause_open:
            self.shop_open = False
            self.set_mouse_capture(False)
        elif self.game_state in {"playing", "glitch"}:
            self.set_mouse_capture(True)

    def apply_default_settings(self) -> None:
        self.mouse_sensitivity = DEFAULT_MOUSE_SENSITIVITY
        self.mouse_smoothing_enabled = DEFAULT_MOUSE_SMOOTHING_ENABLED
        self.fov_degrees = DEFAULT_FOV_DEG
        self.fps_limit = DEFAULT_FPS_LIMIT
        self.shared_money = False
        self.adaptive_quality_enabled = True
        self.apply_fov_setting()
        self.fullscreen_enabled = False
        self.resolution_index = self.find_resolution_index(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.apply_display_settings()
        self.save_user_settings()

    def load_user_settings(self) -> None:
        try:
            raw = self.settings_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        sensitivity = payload.get("mouse_sensitivity")
        if isinstance(sensitivity, (float, int)):
            self.mouse_sensitivity = clamp(float(sensitivity), MIN_SENSITIVITY, MAX_SENSITIVITY)

        smoothing = payload.get("mouse_smoothing_enabled")
        if isinstance(smoothing, bool):
            self.mouse_smoothing_enabled = smoothing

        shared_money = payload.get("shared_money")
        if isinstance(shared_money, bool):
            self.shared_money = shared_money

        adaptive_quality = payload.get("adaptive_quality_enabled")
        if isinstance(adaptive_quality, bool):
            self.adaptive_quality_enabled = adaptive_quality

        fov_deg = payload.get("fov_degrees")
        if isinstance(fov_deg, (float, int)):
            self.fov_degrees = int(clamp(float(fov_deg), MIN_FOV_DEG, MAX_FOV_DEG))

        fps_cap = payload.get("fps_limit")
        if isinstance(fps_cap, (float, int)):
            self.fps_limit = int(clamp(float(fps_cap), MIN_FPS_CAP, MAX_FPS_CAP))

        fullscreen = payload.get("fullscreen_enabled")
        if isinstance(fullscreen, bool):
            self.fullscreen_enabled = fullscreen

        resolution = payload.get("resolution")
        if isinstance(resolution, list) and len(resolution) == 2:
            try:
                width = max(MIN_RENDER_WIDTH, int(resolution[0]))
                height = max(MIN_RENDER_HEIGHT, int(resolution[1]))
                self.resolution_index = self.find_resolution_index(width, height)
            except (TypeError, ValueError):
                pass

    def save_user_settings(self) -> None:
        width, height = self.available_resolutions[self.resolution_index]
        payload = {
            "mouse_sensitivity": round(self.mouse_sensitivity, 4),
            "mouse_smoothing_enabled": bool(self.mouse_smoothing_enabled),
            "shared_money": bool(self.shared_money),
            "adaptive_quality_enabled": bool(self.adaptive_quality_enabled),
            "fov_degrees": int(self.fov_degrees),
            "fps_limit": int(self.fps_limit),
            "fullscreen_enabled": bool(self.fullscreen_enabled),
            "resolution": [int(width), int(height)],
        }
        try:
            self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def load_profile(self) -> None:
        try:
            raw = self.profile_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(payload, dict):
            return

        self.profile_level = max(1, int(payload.get("level", self.profile_level)))
        self.profile_xp = max(0, int(payload.get("xp", self.profile_xp)))
        self.perk_points = max(0, int(payload.get("perk_points", self.perk_points)))
        self.attachment_tier = max(0, int(payload.get("attachment_tier", self.attachment_tier)))

        perks = payload.get("perks")
        if isinstance(perks, dict):
            for key in self.perks.keys():
                value = perks.get(key)
                if isinstance(value, int):
                    self.perks[key] = max(0, min(8, value))

    def save_profile(self) -> None:
        payload = {
            "level": int(self.profile_level),
            "xp": int(self.profile_xp),
            "perk_points": int(self.perk_points),
            "attachment_tier": int(self.attachment_tier),
            "perks": dict(self.perks),
        }
        try:
            self.profile_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def xp_to_next_level(self) -> int:
        return 100 + self.profile_level * 65

    def gain_xp(self, amount: int) -> None:
        if amount <= 0:
            return
        self.profile_xp += amount
        leveled = False
        while self.profile_xp >= self.xp_to_next_level():
            self.profile_xp -= self.xp_to_next_level()
            self.profile_level += 1
            self.perk_points += 1
            leveled = True
            if self.profile_level % 3 == 0:
                self.attachment_tier += 1
        if leveled:
            self.net_status = f"Level up! L{self.profile_level} | Perk points: {self.perk_points}"
        self.save_profile()

    def apply_perk_choice(self, perk_key: str) -> None:
        if self.perk_points <= 0:
            return
        if perk_key not in self.perks:
            return
        if self.perks[perk_key] >= 8:
            return
        self.perks[perk_key] += 1
        self.perk_points -= 1
        self.save_profile()

    def get_max_health_cap(self) -> float:
        return 100.0 + self.perks["vitality"] * 10.0

    def get_move_speed_multiplier(self) -> float:
        return 1.0 + self.perks["mobility"] * 0.05

    def get_regen_multiplier(self) -> float:
        return 1.0 + self.perks["regen"] * 0.14

    def get_weapon_damage_multiplier(self) -> float:
        return 1.0 + self.perks["weapon"] * 0.06 + self.attachment_tier * 0.02

    def get_spread_multiplier(self) -> float:
        return max(0.6, 1.0 - self.attachment_tier * 0.03)

    def get_recoil_multiplier(self) -> float:
        return max(0.65, 1.0 - self.attachment_tier * 0.025)

    def _build_floor_cells(self) -> None:
        self.floor_cells: list[tuple[int, int]] = []
        for y, row in enumerate(WORLD_MAP):
            for x, cell in enumerate(row):
                if cell == ".":
                    self.floor_cells.append((x, y))

    def get_reachable_floor_cells(self) -> list[tuple[int, int]]:
        start_x = int(self.player_x)
        start_y = int(self.player_y)
        if self.is_wall(start_x + 0.5, start_y + 0.5):
            return list(self.floor_cells)

        queue = deque([(start_x, start_y)])
        visited: set[tuple[int, int]] = {(start_x, start_y)}
        reachable: list[tuple[int, int]] = []

        while queue:
            x, y = queue.popleft()
            reachable.append((x, y))
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = x + ox
                ny = y + oy
                if (nx, ny) in visited:
                    continue
                if ny < 0 or ny >= len(WORLD_MAP) or nx < 0 or nx >= len(WORLD_MAP[0]):
                    continue
                if WORLD_MAP[ny][nx] == "#":
                    continue
                visited.add((nx, ny))
                queue.append((nx, ny))

        if not reachable:
            return list(self.floor_cells)
        return reachable

    def _build_cover_points(self) -> None:
        self.cover_points: list[tuple[float, float]] = []
        for x, y in self.floor_cells:
            walls = 0
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if self.is_wall(x + ox + 0.5, y + oy + 0.5):
                    walls += 1
            if walls >= 2:
                self.cover_points.append((x + 0.5, y + 0.5))

    def pick_spawn_far_from_point(self, ref_x: float, ref_y: float, min_dist: float) -> tuple[float, float]:
        reachable_cells = self.get_reachable_floor_cells()
        candidates: list[tuple[float, float, float]] = []
        for cell_x, cell_y in reachable_cells:
            x = cell_x + 0.5
            y = cell_y + 0.5
            if not self.can_move(x, y, 0.24):
                continue
            d = distance(x, y, ref_x, ref_y)
            candidates.append((x, y, d))

        if not candidates:
            return self.player_x, self.player_y

        far = [item for item in candidates if item[2] >= min_dist]
        if far:
            x, y, _ = random.choice(far)
            return x, y

        x, y, _ = max(candidates, key=lambda item: item[2])
        return x, y

    def send_client_action(self, action_type: str, weapon: str) -> None:
        if self.coop_client is None:
            return
        self.coop_client.send({"type": action_type, "weapon": weapon})

    def process_host_network_events(self) -> None:
        if self.coop_server is None:
            return

        for event in self.coop_server.poll():
            evt = event.get("event")
            player_id = event.get("player_id", "")
            if evt == "connect":
                spawn_x, spawn_y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 6.0)
                remote = RemotePlayer(
                    player_id=player_id,
                    name=f"Teammate {player_id}",
                    x=spawn_x,
                    y=spawn_y,
                    angle=random.uniform(0.0, math.tau),
                )
                remote.clip = {weapon: 0 for weapon in WEAPON_ORDER}
                remote.clip["pistol"] = int(WEAPON_DATA["pistol"]["mag_size"])
                self.remote_players[player_id] = remote
            elif evt == "disconnect":
                self.remote_players.pop(player_id, None)
            elif evt == "message":
                message = event.get("message", {})
                remote = self.remote_players.get(player_id)
                if remote is None:
                    continue

                msg_type = message.get("type")
                if msg_type == "hello":
                    name = str(message.get("name", "")).strip()
                    if name:
                        remote.name = name[:18]
                elif msg_type == "input":
                    allowed = {"w", "a", "s", "d", "shift_l", "shift_r", "left", "right", "e", "r", "q"}
                    remote.keys = {k for k in message.get("keys", []) if k in allowed}
                    try:
                        remote.angle = normalize_angle(float(message.get("angle", remote.angle)))
                    except (TypeError, ValueError):
                        pass
                    remote.shooting = bool(message.get("shoot", False))
                elif msg_type == "buy_or_equip":
                    weapon = message.get("weapon")
                    if isinstance(weapon, str) and weapon in WEAPON_ORDER:
                        self.buy_or_equip_remote(remote, weapon)

        self.net_status = f"Hosting co-op ({1 + len(self.remote_players)} players)"

    def process_client_network_events(self) -> None:
        if self.coop_client is None:
            return

        for event in self.coop_client.poll():
            evt = event.get("event")
            if evt == "disconnect":
                self.client_connected = False
                self.net_status = "Disconnected from host"
                continue

            message = event.get("message", {})
            msg_type = message.get("type")
            if msg_type == "welcome":
                self.player_id = str(message.get("player_id", ""))
                self.client_connected = True
            elif msg_type == "snapshot":
                self.apply_snapshot(message)

    def apply_snapshot(self, payload: dict) -> None:
        you_id = str(payload.get("you_id", "")).strip()
        if you_id:
            self.player_id = you_id

        you = payload.get("you", {})
        self.player_x = float(you.get("x", self.player_x))
        self.player_y = float(you.get("y", self.player_y))
        self.player_angle = normalize_angle(float(you.get("angle", self.player_angle)))
        self.player_health = float(you.get("health", self.player_health))
        self.player_downed = bool(you.get("downed", self.player_downed))
        self.player_money = int(you.get("money", self.player_money))
        self.current_weapon = str(you.get("weapon", self.current_weapon))

        ammo_data = you.get("ammo")
        if isinstance(ammo_data, dict):
            for weapon in WEAPON_ORDER:
                if weapon in ammo_data:
                    self.ammo[weapon] = int(ammo_data[weapon])

        clip_data = you.get("clip")
        if isinstance(clip_data, dict):
            for weapon in WEAPON_ORDER:
                if weapon in clip_data:
                    self.clip[weapon] = int(clip_data[weapon])

        owned_data = you.get("owned")
        if isinstance(owned_data, dict):
            for weapon in WEAPON_ORDER:
                if weapon in owned_data:
                    self.owned_weapons[weapon] = bool(owned_data[weapon])

        self.wave = int(payload.get("wave", self.wave))
        self.game_state = str(payload.get("game_state", self.game_state))
        self.objective_type = str(payload.get("objective_type", self.objective_type))
        self.objective_timer = float(payload.get("objective_timer", self.objective_timer))
        self.shared_money = bool(payload.get("shared_money", self.shared_money))
        zone = payload.get("objective_zone")
        if isinstance(zone, (list, tuple)) and len(zone) == 3:
            try:
                self.objective_zone = (float(zone[0]), float(zone[1]), float(zone[2]))
            except (TypeError, ValueError):
                self.objective_zone = None
        elif zone is None:
            self.objective_zone = None

        ping = payload.get("ping")
        if isinstance(ping, dict):
            try:
                self.team_ping = (
                    float(ping.get("x", 0.0)),
                    float(ping.get("y", 0.0)),
                    float(ping.get("ttl", 0.0)),
                    str(ping.get("owner", "TEAM")),
                )
            except (TypeError, ValueError):
                self.team_ping = None
        elif ping is None:
            self.team_ping = None

        self.bots = []
        for item in payload.get("bots", []):
            self.bots.append(
                Bot(
                    x=float(item.get("x", 0.0)),
                    y=float(item.get("y", 0.0)),
                    health=float(item.get("health", 100.0)),
                    speed=float(item.get("speed", 1.2)),
                    kind=str(item.get("kind", "grunt")),
                    state=str(item.get("state", "advance")),
                    alive=bool(item.get("alive", True)),
                    attack_range=float(item.get("attack_range", 11.5)),
                    hit_bonus=float(item.get("hit_bonus", 0.0)),
                    damage_min=int(item.get("damage_min", 4)),
                    damage_max=int(item.get("damage_max", 9)),
                    money_multiplier=float(item.get("money_multiplier", 1.0)),
                )
            )

        self.money_drops = []
        for item in payload.get("drops", []):
            self.money_drops.append(
                MoneyDrop(
                    x=float(item.get("x", 0.0)),
                    y=float(item.get("y", 0.0)),
                    value=int(item.get("value", 0)),
                    ttl=float(item.get("ttl", 24.0)),
                )
            )

        seen: set[str] = set()
        for item in payload.get("players", []):
            player_id = str(item.get("id", ""))
            if player_id == self.player_id:
                continue
            tx = float(item.get("x", 0.0))
            ty = float(item.get("y", 0.0))
            ta = float(item.get("angle", 0.0))
            th = float(item.get("health", 0.0))
            tw = str(item.get("weapon", "pistol"))
            tn = str(item.get("name", "Teammate"))
            td = bool(item.get("downed", False))
            self.remote_interp_targets[player_id] = (tx, ty, ta, th, tw, td, tn)
            view = self.remote_render_map.get(player_id)
            if view is None:
                view = TeammateView(player_id=player_id, name=tn, x=tx, y=ty, angle=ta, health=th, weapon=tw, downed=td)
                self.remote_render_map[player_id] = view
            seen.add(player_id)

        for player_id in list(self.remote_render_map.keys()):
            if player_id not in seen:
                self.remote_render_map.pop(player_id, None)
                self.remote_interp_targets.pop(player_id, None)

        self.remote_render_players = list(self.remote_render_map.values())

        self.net_status = f"Connected teammates: {len(self.remote_render_players)}"

    def update_remote_interpolation(self, dt: float) -> None:
        if self.net_mode != "client":
            return
        blend = clamp(dt * 10.0, 0.0, 1.0)
        for player_id, view in self.remote_render_map.items():
            target = self.remote_interp_targets.get(player_id)
            if target is None:
                continue
            tx, ty, ta, th, tw, td, tn = target
            view.x += (tx - view.x) * blend
            view.y += (ty - view.y) * blend
            da = normalize_angle(ta - view.angle)
            if da > math.pi:
                da -= math.tau
            view.angle = normalize_angle(view.angle + da * blend)
            view.health = th
            view.weapon = tw
            view.name = tn
            view.downed = td
        self.remote_render_players = list(self.remote_render_map.values())

    def send_client_input(self, now: float) -> None:
        if self.coop_client is None or not self.client_connected:
            return
        if now - self.last_net_send < self.net_send_interval:
            return

        self.last_net_send = now
        allowed = ["w", "a", "s", "d", "shift_l", "shift_r", "left", "right", "e", "r", "q"]
        pressed = [key for key in allowed if key in self.keys]
        self.coop_client.send(
            {
                "type": "input",
                "keys": pressed,
                "angle": self.player_angle,
                "shoot": bool(
                    self.mouse_down
                    and not self.shop_open
                    and not self.pause_open
                    and not self.player_downed
                    and self.game_state == "playing"
                ),
            }
        )

    def serialize_remote(self, remote: RemotePlayer) -> dict:
        return {
            "id": remote.player_id,
            "name": remote.name,
            "x": remote.x,
            "y": remote.y,
            "angle": remote.angle,
            "health": remote.health,
            "downed": remote.downed,
            "money": remote.money,
            "weapon": remote.current_weapon,
            "ammo": dict(remote.ammo),
            "clip": dict(remote.clip),
            "owned": dict(remote.owned_weapons),
        }

    def serialize_local(self) -> dict:
        return {
            "id": "host",
            "name": self.player_name,
            "x": self.player_x,
            "y": self.player_y,
            "angle": self.player_angle,
            "health": self.player_health,
            "downed": self.player_downed,
            "money": self.player_money,
            "weapon": self.current_weapon,
            "ammo": dict(self.ammo),
            "clip": dict(self.clip),
            "owned": dict(self.owned_weapons),
        }

    def broadcast_snapshot(self, now: float) -> None:
        if self.coop_server is None:
            return
        if now - self.last_net_send < self.net_send_interval:
            return
        self.last_net_send = now

        players = [self.serialize_local()] + [self.serialize_remote(p) for p in self.remote_players.values()]
        bots = [
            {
                "x": b.x,
                "y": b.y,
                "health": b.health,
                "speed": b.speed,
                "kind": b.kind,
                "state": b.state,
                "alive": b.alive,
                "attack_range": b.attack_range,
                "hit_bonus": b.hit_bonus,
                "damage_min": b.damage_min,
                "damage_max": b.damage_max,
                "money_multiplier": b.money_multiplier,
            }
            for b in self.bots
            if b.alive
        ]
        drops = [{"x": d.x, "y": d.y, "value": d.value, "ttl": d.ttl} for d in self.money_drops]
        ping = None
        if self.team_ping is not None:
            ping = {
                "x": self.team_ping[0],
                "y": self.team_ping[1],
                "ttl": self.team_ping[2],
                "owner": self.team_ping[3],
            }

        for remote in self.remote_players.values():
            payload = {
                "type": "snapshot",
                "you_id": remote.player_id,
                "you": self.serialize_remote(remote),
                "players": players,
                "bots": bots,
                "drops": drops,
                "wave": self.wave,
                "game_state": self.game_state,
                "objective_type": self.objective_type,
                "objective_timer": self.objective_timer,
                "objective_zone": self.objective_zone,
                "shared_money": self.shared_money,
                "ping": ping,
            }
            self.coop_server.send(remote.player_id, payload)

    def buy_or_equip_remote(self, remote: RemotePlayer, weapon: str) -> None:
        if self.game_state != "playing":
            return

        if remote.owned_weapons[weapon]:
            remote.current_weapon = weapon
            return

        config = WEAPON_DATA[weapon]
        if remote.money < config["cost"]:
            return

        remote.money -= config["cost"]
        remote.owned_weapons[weapon] = True
        remote.ammo[weapon] += config["ammo_pack"]
        if remote.clip[weapon] <= 0 and not config["infinite"]:
            needed = int(config["mag_size"])
            loaded = min(needed, remote.ammo[weapon])
            remote.clip[weapon] += loaded
            remote.ammo[weapon] -= loaded
        remote.current_weapon = weapon
        remote.revive_progress = 0.0

    def update_remote_players(self, dt: float, now: float) -> None:
        for remote in self.remote_players.values():
            if remote.downed:
                if "e" in remote.keys and self.player_downed and distance(remote.x, remote.y, self.player_x, self.player_y) <= 1.7:
                    self.player_revive_progress += dt
                    if self.player_revive_progress >= 2.3:
                        self.player_downed = False
                        self.player_bleed_out = 0.0
                        self.player_revive_progress = 0.0
                        self.player_health = 40.0
                continue
            if remote.health <= 0:
                continue

            remote.time_since_damage += dt
            speed = 3.2
            if "shift_l" in remote.keys or "shift_r" in remote.keys:
                speed = 4.2

            move_x = 0.0
            move_y = 0.0
            sin_a = math.sin(remote.angle)
            cos_a = math.cos(remote.angle)

            if "w" in remote.keys:
                move_x += cos_a * speed * dt
                move_y += sin_a * speed * dt
            if "s" in remote.keys:
                move_x -= cos_a * speed * dt
                move_y -= sin_a * speed * dt
            if "a" in remote.keys:
                move_x += math.cos(remote.angle - math.pi / 2) * speed * dt
                move_y += math.sin(remote.angle - math.pi / 2) * speed * dt
            if "d" in remote.keys:
                move_x += math.cos(remote.angle + math.pi / 2) * speed * dt
                move_y += math.sin(remote.angle + math.pi / 2) * speed * dt

            if "left" in remote.keys:
                remote.angle -= 1.7 * dt
            if "right" in remote.keys:
                remote.angle += 1.7 * dt
            remote.angle = normalize_angle(remote.angle)

            next_x = remote.x + move_x
            next_y = remote.y + move_y
            if self.can_move(next_x, remote.y, PLAYER_RADIUS):
                remote.x = next_x
            if self.can_move(remote.x, next_y, PLAYER_RADIUS):
                remote.y = next_y

            if remote.health < 100.0 and remote.time_since_damage >= HEALTH_REGEN_DELAY:
                remote.health = min(100.0, remote.health + HEALTH_REGEN_RATE * dt)

            if "q" in remote.keys:
                self.team_ping = (remote.x, remote.y, 5.5, remote.name)

            self.handle_remote_shooting(remote, now)

    def handle_remote_shooting(self, remote: RemotePlayer, now: float) -> None:
        if not remote.shooting or remote.health <= 0 or remote.downed:
            return
        if now < remote.next_fire_at:
            return

        weapon = remote.current_weapon
        config = WEAPON_DATA[weapon]
        if not config["infinite"] and remote.clip[weapon] <= 0:
            needed = int(config["mag_size"])
            loaded = min(needed, remote.ammo[weapon])
            remote.clip[weapon] += loaded
            remote.ammo[weapon] -= loaded
        if not config["infinite"] and remote.clip[weapon] <= 0:
            remote.current_weapon = "pistol"
            return

        remote.next_fire_at = now + config["fire_rate"]
        if not config["infinite"]:
            remote.clip[weapon] = max(0, remote.clip[weapon] - 1)

        if weapon == "rpg":
            self.game_state = "glitch"
            self.glitch_timer = 1.2
            return

        for _ in range(config["pellets"]):
            shot_angle = remote.angle + random.uniform(-config["spread"], config["spread"])
            target, _ = self.get_first_bot_hit_from(remote.x, remote.y, shot_angle, config["range"])
            if target is None:
                continue
            target.health -= config["damage"]
            if target.health <= 0 and target.alive:
                self.kill_bot(target)

        if not config["infinite"] and remote.clip[weapon] <= 0 and remote.ammo[weapon] <= 0:
            remote.current_weapon = "pistol"

    def all_humans_dead(self) -> bool:
        if self.player_health > 0 or self.player_downed:
            return False
        for remote in self.remote_players.values():
            if remote.health > 0 or remote.downed:
                return False
        return True

    def on_key_down(self, event: tk.Event) -> None:
        key = event.keysym.lower()
        if key == "escape":
            if self.game_state == "bsod":
                self.on_close()
                return

            if self.shop_open:
                self.shop_open = False
                if self.game_state == "playing" and not self.pause_open:
                    self.set_mouse_capture(True)
                return

            self.toggle_pause_menu()
            return

        if self.pause_open:
            if key in {"return", "space"}:
                self.toggle_pause_menu()
            elif key == "q":
                self.on_close()
            return

        self.keys.add(key)

        if key == "b" and self.game_state == "playing":
            self.shop_open = not self.shop_open
            self.set_mouse_capture(not self.shop_open)

        if key in {"1", "2", "3", "4"} and self.game_state == "playing":
            index = int(key) - 1
            if 0 <= index < len(WEAPON_ORDER):
                weapon = WEAPON_ORDER[index]
                if self.net_mode == "client":
                    self.send_client_action("buy_or_equip", weapon)
                else:
                    self.buy_or_equip(weapon)

        if key == "r" and self.game_state == "playing":
            if self.net_mode != "client":
                self.start_reload(time.perf_counter())

        if key == "q" and self.game_state == "playing":
            self.place_team_ping()

        if key == "f1":
            self.apply_perk_choice("vitality")
        if key == "f2":
            self.apply_perk_choice("mobility")
        if key == "f3":
            self.apply_perk_choice("regen")
        if key == "f4":
            self.apply_perk_choice("weapon")

        if key == "r" and self.game_state in {"dead", "bsod"}:
            if self.net_mode == "client":
                return
            self.reset_game()

    def on_key_up(self, event: tk.Event) -> None:
        key = event.keysym.lower()
        self.keys.discard(key)

    def on_focus_in(self, _event: tk.Event) -> None:
        self.focused = True
        if self.mouse_locked:
            self.canvas.configure(cursor="none")
            self.center_mouse()
            self.clip_cursor_to_canvas()

    def on_focus_out(self, _event: tk.Event) -> None:
        self.focused = False
        self.smoothed_mouse_dx = 0.0
        self.next_warp_allowed_at = 0.0
        self.release_cursor_clip()
        self.canvas.configure(cursor="arrow")

    def on_window_configure(self, _event: tk.Event) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if abs(width - WIDTH) > 1 or abs(height - HEIGHT) > 1:
            self.update_render_metrics(width, height)
            if not self.fullscreen_enabled:
                self.resolution_index = self.find_resolution_index(WIDTH, HEIGHT)
        if self.mouse_locked and self.focused:
            self.clip_cursor_to_canvas()

    def on_close(self) -> None:
        self.save_user_settings()
        self.save_profile()
        self.release_cursor_clip()
        if self.coop_server is not None:
            self.coop_server.stop()
        if self.coop_client is not None:
            self.coop_client.stop()
        self.root.destroy()

    def set_mouse_capture(self, capture: bool) -> None:
        self.mouse_locked = capture
        self.smoothed_mouse_dx = 0.0
        self.next_warp_allowed_at = 0.0
        if not self.focused:
            return

        if capture:
            self.canvas.configure(cursor="none")
            self.center_mouse()
            self.clip_cursor_to_canvas()
        else:
            self.release_cursor_clip()
            self.canvas.configure(cursor="arrow")

    def center_mouse(self) -> None:
        if HAS_WIN32 and self.use_warp_mouse:
            cx = self.canvas.winfo_rootx() + self.canvas.winfo_width() // 2
            cy = self.canvas.winfo_rooty() + self.canvas.winfo_height() // 2
            ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
            self.last_mouse_x = self.canvas.winfo_width() // 2
            self.last_mouse_y = self.canvas.winfo_height() // 2
            return

        if self.use_warp_mouse and HAS_MACOS_CURSOR_WARP and _macos_app_services is not None:
            cx = self.canvas.winfo_rootx() + self.canvas.winfo_width() // 2
            cy = self.canvas.winfo_rooty() + self.canvas.winfo_height() // 2
            result = _macos_app_services.CGWarpMouseCursorPosition(_CGPoint(float(cx), float(cy)))
            if result != 0:
                self.use_warp_mouse = False
                self.sync_mouse_reference()
                return
            self.last_mouse_x = self.canvas.winfo_width() // 2
            self.last_mouse_y = self.canvas.winfo_height() // 2
            return

        self.sync_mouse_reference()

    def sync_mouse_reference(self) -> None:
        rel_x = self.root.winfo_pointerx() - self.canvas.winfo_rootx()
        rel_y = self.root.winfo_pointery() - self.canvas.winfo_rooty()
        max_x = max(0, self.canvas.winfo_width() - 1)
        max_y = max(0, self.canvas.winfo_height() - 1)
        self.last_mouse_x = int(clamp(rel_x, 0, max_x))
        self.last_mouse_y = int(clamp(rel_y, 0, max_y))

    def clip_cursor_to_canvas(self) -> None:
        if not HAS_WIN32:
            return
        left = self.canvas.winfo_rootx()
        top = self.canvas.winfo_rooty()
        right = left + self.canvas.winfo_width()
        bottom = top + self.canvas.winfo_height()
        rect = wintypes.RECT(left, top, right, bottom)
        ctypes.windll.user32.ClipCursor(ctypes.byref(rect))

    def release_cursor_clip(self) -> None:
        if HAS_WIN32:
            ctypes.windll.user32.ClipCursor(None)

    def on_mouse_move(self, event: tk.Event) -> None:
        if not self.mouse_locked:
            self.smoothed_mouse_dx = 0.0
            self.last_mouse_x = event.x
            self.last_mouse_y = event.y
            return

        now = time.perf_counter()
        dx = event.x - self.last_mouse_x
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

        if dx == 0:
            return

        dx = clamp(dx, -180, 180)
        if self.mouse_smoothing_enabled:
            alpha = self.mouse_smoothing_strength
            self.smoothed_mouse_dx = self.smoothed_mouse_dx * (1.0 - alpha) + dx * alpha
            look_dx = self.smoothed_mouse_dx
        else:
            self.smoothed_mouse_dx = dx
            look_dx = dx

        self.player_angle += look_dx * self.mouse_sensitivity
        self.player_angle = normalize_angle(self.player_angle)
        if self.use_warp_mouse:
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            near_edge = (
                event.x < MOUSE_WARP_EDGE_MARGIN
                or event.x > width - MOUSE_WARP_EDGE_MARGIN
                or event.y < MOUSE_WARP_EDGE_MARGIN
                or event.y > height - MOUSE_WARP_EDGE_MARGIN
            )
            if near_edge and now >= self.next_warp_allowed_at:
                self.center_mouse()
                self.next_warp_allowed_at = now + MOUSE_WARP_INTERVAL_SECONDS

    def on_mouse_down(self, event: tk.Event) -> None:
        if self.pause_open:
            self.handle_pause_click(event.x, event.y)
            return

        button = getattr(event, "num", 1)
        if button in {2, 3}:
            if self.game_state == "playing":
                self.place_team_ping()
            return

        self.mouse_down = True
        if self.shop_open and self.game_state == "playing":
            slot = self.shop_slot_from_mouse()
            if slot is not None:
                weapon = WEAPON_ORDER[slot]
                if self.net_mode == "client":
                    self.send_client_action("buy_or_equip", weapon)
                else:
                    self.buy_or_equip(weapon)

    def on_mouse_up(self, _event: tk.Event) -> None:
        self.mouse_down = False

    def handle_pause_click(self, mouse_x: int, mouse_y: int) -> None:
        for hitbox in self.pause_hitboxes:
            if hitbox.contains(mouse_x, mouse_y):
                self.handle_pause_action(hitbox.action)
                break

    def handle_pause_action(self, action: str) -> None:
        should_save = False
        if action == "sens_down":
            self.mouse_sensitivity = max(
                MIN_SENSITIVITY,
                round(self.mouse_sensitivity - SENSITIVITY_STEP, 4),
            )
            should_save = True
        elif action == "sens_up":
            self.mouse_sensitivity = min(
                MAX_SENSITIVITY,
                round(self.mouse_sensitivity + SENSITIVITY_STEP, 4),
            )
            should_save = True
        elif action == "smoothing":
            self.mouse_smoothing_enabled = not self.mouse_smoothing_enabled
            self.smoothed_mouse_dx = 0.0
            should_save = True
        elif action == "shared_money":
            self.shared_money = not self.shared_money
            should_save = True
        elif action == "adaptive_quality":
            self.adaptive_quality_enabled = not self.adaptive_quality_enabled
            should_save = True
        elif action == "fov_down":
            self.fov_degrees = max(MIN_FOV_DEG, self.fov_degrees - FOV_STEP_DEG)
            self.apply_fov_setting()
            should_save = True
        elif action == "fov_up":
            self.fov_degrees = min(MAX_FOV_DEG, self.fov_degrees + FOV_STEP_DEG)
            self.apply_fov_setting()
            should_save = True
        elif action == "fps_down":
            self.fps_limit = max(MIN_FPS_CAP, self.fps_limit - FPS_STEP)
            should_save = True
        elif action == "fps_up":
            self.fps_limit = min(MAX_FPS_CAP, self.fps_limit + FPS_STEP)
            should_save = True
        elif action == "res_prev":
            self.resolution_index = (self.resolution_index - 1) % len(self.available_resolutions)
            if not self.fullscreen_enabled:
                self.apply_display_settings()
            should_save = True
        elif action == "res_next":
            self.resolution_index = (self.resolution_index + 1) % len(self.available_resolutions)
            if not self.fullscreen_enabled:
                self.apply_display_settings()
            should_save = True
        elif action == "fullscreen":
            self.fullscreen_enabled = not self.fullscreen_enabled
            self.apply_display_settings()
            should_save = True
        elif action == "defaults":
            self.apply_default_settings()
        elif action == "resume":
            self.toggle_pause_menu()
        elif action == "quit":
            self.on_close()

        if should_save:
            self.save_user_settings()

    def place_team_ping(self) -> None:
        if self.net_mode == "host":
            owner = self.player_name
        else:
            owner = "YOU"
        distance_out = 4.2
        px = self.player_x + math.cos(self.player_angle) * distance_out
        py = self.player_y + math.sin(self.player_angle) * distance_out
        if self.is_wall(px, py):
            px = self.player_x + math.cos(self.player_angle) * 2.2
            py = self.player_y + math.sin(self.player_angle) * 2.2
        self.team_ping = (px, py, 5.5, owner)

    def start_reload(self, now: float) -> None:
        weapon = self.current_weapon
        config = WEAPON_DATA[weapon]
        if config["infinite"]:
            return
        if self.current_reload_weapon is not None:
            return
        mag_size = int(config["mag_size"])
        if self.clip[weapon] >= mag_size:
            return
        if self.ammo[weapon] <= 0:
            return
        self.current_reload_weapon = weapon
        self.reload_end_at = now + float(config["reload_time"])

    def update_reload(self, now: float) -> None:
        if self.current_reload_weapon is None:
            return
        if now < self.reload_end_at:
            return
        weapon = self.current_reload_weapon
        config = WEAPON_DATA[weapon]
        mag_size = int(config["mag_size"])
        needed = max(0, mag_size - self.clip[weapon])
        loaded = min(needed, self.ammo[weapon])
        self.clip[weapon] += loaded
        self.ammo[weapon] -= loaded
        self.current_reload_weapon = None

    def buy_or_equip(self, weapon: str) -> None:
        if self.game_state != "playing":
            return

        config = WEAPON_DATA[weapon]
        if self.owned_weapons[weapon]:
            self.current_weapon = weapon
            self.current_reload_weapon = None
            return

        if self.player_money < config["cost"]:
            return

        self.player_money -= config["cost"]
        self.owned_weapons[weapon] = True
        self.ammo[weapon] += config["ammo_pack"]
        self.current_weapon = weapon
        if not config["infinite"] and self.clip[weapon] <= 0:
            self.start_reload(time.perf_counter())

    def loop(self) -> None:
        now = time.perf_counter()
        dt = min(now - self.last_time, 0.05)
        self.last_time = now
        self.adjust_render_quality(dt, now)

        self.update(dt, now)
        self.render(now)

        ms = int(1000 / max(MIN_FPS_CAP, self.fps_limit))
        self.root.after(ms, self.loop)

    def adjust_render_quality(self, dt: float, now: float) -> None:
        global RAY_COUNT
        self.frame_dt_avg = self.frame_dt_avg * 0.9 + dt * 0.1
        if not self.adaptive_quality_enabled:
            return
        if now - self.last_quality_adjust < 0.55:
            return

        target_dt = 1.0 / max(MIN_FPS_CAP, min(120, self.fps_limit))
        if self.frame_dt_avg > target_dt * 1.2 and RAY_COUNT > 160:
            RAY_COUNT = max(160, RAY_COUNT - 10)
            self.last_quality_adjust = now
        elif self.frame_dt_avg < target_dt * 0.82 and RAY_COUNT < self.base_ray_target:
            RAY_COUNT = min(self.base_ray_target, RAY_COUNT + 8)
            self.last_quality_adjust = now

    def update(self, dt: float, now: float) -> None:
        if self.team_ping is not None:
            self.team_ping = (self.team_ping[0], self.team_ping[1], self.team_ping[2] - dt, self.team_ping[3])
            if self.team_ping[2] <= 0:
                self.team_ping = None
        self.spread_heat = max(0.0, self.spread_heat - dt * 1.5)
        self.damage_direction_timer = max(0.0, self.damage_direction_timer - dt)

        if self.pause_open:
            if self.net_mode == "client":
                self.process_client_network_events()
                self.update_remote_interpolation(dt)
            elif self.net_mode == "host":
                self.process_host_network_events()
                self.broadcast_snapshot(now)
            self.damage_flash = max(0.0, self.damage_flash - dt * 2.8)
            self.muzzle_flash_timer = max(0.0, self.muzzle_flash_timer - dt * 5.0)
            self.weapon_kick = max(0.0, self.weapon_kick - dt * 6.5)
            return

        if self.net_mode == "client":
            self.process_client_network_events()
            self.update_remote_interpolation(dt)
            self.send_client_input(now)
            if self.game_state in {"dead", "bsod"}:
                self.set_mouse_capture(False)

            self.damage_flash = max(0.0, self.damage_flash - dt * 2.8)
            self.muzzle_flash_timer = max(0.0, self.muzzle_flash_timer - dt * 5.0)
            self.weapon_kick = max(0.0, self.weapon_kick - dt * 6.5)
            return

        if self.net_mode == "host":
            self.process_host_network_events()

        if self.game_state == "playing":
            self.update_reload(now)
            self.update_downed_state(dt)
            self.time_since_damage += dt
            if self.player_health > 0 and not self.player_downed:
                self.update_player_movement(dt)
                self.handle_shooting(now)
            if self.net_mode == "host":
                self.update_remote_players(dt, now)

            self.update_bots(dt)
            self.update_drops(dt)
            self.update_objective(dt)

            if self.player_health <= 0 and not self.player_downed:
                self.player_health = 0
            elif self.player_health < self.get_max_health_cap() and self.time_since_damage >= HEALTH_REGEN_DELAY:
                regen = HEALTH_REGEN_RATE * self.get_regen_multiplier()
                self.player_health = min(self.get_max_health_cap(), self.player_health + regen * dt)

            if self.all_humans_dead():
                self.game_state = "dead"
                self.set_mouse_capture(False)

            objective_complete = False
            if self.objective_type == "defend_zone":
                objective_complete = self.objective_timer <= 0
            else:
                objective_complete = self.alive_bots() == 0

            if objective_complete:
                if self.wave_timer <= 0:
                    self.wave_timer = 3.2
                self.wave_timer -= dt
                if self.wave_timer <= 0:
                    self.spawn_wave()
            else:
                self.wave_timer = 0.0

        elif self.game_state == "glitch":
            self.glitch_timer -= dt
            if self.glitch_timer <= 0:
                self.game_state = "bsod"
                self.bsod_started_at = now
                self.set_mouse_capture(False)

        self.damage_flash = max(0.0, self.damage_flash - dt * 2.8)
        self.muzzle_flash_timer = max(0.0, self.muzzle_flash_timer - dt * 5.0)
        self.weapon_kick = max(0.0, self.weapon_kick - dt * 6.5)

        if self.net_mode == "host":
            self.broadcast_snapshot(now)

    def update_downed_state(self, dt: float) -> None:
        if self.player_downed:
            self.player_bleed_out -= dt
            if self.player_bleed_out <= 0:
                self.player_downed = False
                self.player_health = 0.0
        else:
            self.player_revive_progress = max(0.0, self.player_revive_progress - dt * 1.4)

        if self.net_mode != "host":
            return

        # Local player revives nearby downed teammate.
        if "e" in self.keys and self.player_health > 0 and not self.player_downed:
            best_remote: RemotePlayer | None = None
            best_dist = 999.0
            for remote in self.remote_players.values():
                if not remote.downed:
                    continue
                d = distance(self.player_x, self.player_y, remote.x, remote.y)
                if d < 1.7 and d < best_dist:
                    best_remote = remote
                    best_dist = d
            if best_remote is not None:
                best_remote.revive_progress += dt
                if best_remote.revive_progress >= 2.3:
                    best_remote.downed = False
                    best_remote.bleed_out = 0.0
                    best_remote.revive_progress = 0.0
                    best_remote.health = 40.0
            for remote in self.remote_players.values():
                if remote is not best_remote:
                    remote.revive_progress = max(0.0, remote.revive_progress - dt * 1.6)
        else:
            for remote in self.remote_players.values():
                remote.revive_progress = max(0.0, remote.revive_progress - dt * 1.6)

        for remote in self.remote_players.values():
            if remote.downed:
                remote.bleed_out -= dt
                if remote.bleed_out <= 0:
                    remote.downed = False
                    remote.health = 0.0
            elif remote.health > 0:
                remote.revive_progress = max(0.0, remote.revive_progress - dt * 1.4)

    def update_objective(self, dt: float) -> None:
        if self.objective_type == "defend_zone" and self.objective_zone is not None:
            zx, zy, radius = self.objective_zone
            in_zone = False
            if self.player_health > 0 and not self.player_downed and distance(self.player_x, self.player_y, zx, zy) <= radius:
                in_zone = True
            if self.net_mode == "host":
                for remote in self.remote_players.values():
                    if remote.health > 0 and not remote.downed and distance(remote.x, remote.y, zx, zy) <= radius:
                        in_zone = True
                        break
            if in_zone:
                self.objective_timer = max(0.0, self.objective_timer - dt)

            if self.objective_timer <= 0 and self.wave_cleared_award_pending:
                self.wave_cleared_award_pending = False
                reward = 90 + self.wave * 8
                self.award_money("host", reward)
                self.gain_xp(45 + self.wave * 4)
                self.bots.clear()
                self.wave_timer = 3.2
        else:
            if self.alive_bots() == 0 and self.wave_cleared_award_pending:
                self.wave_cleared_award_pending = False
                self.gain_xp(30 + self.wave * 3)

    def apply_damage_to_host(self, dmg: float, source_x: float, source_y: float) -> None:
        if self.player_health <= 0 and not self.player_downed:
            return
        self.last_damage_from = math.atan2(source_y - self.player_y, source_x - self.player_x)
        self.damage_direction_timer = 0.9

        if self.player_downed:
            self.player_bleed_out -= dmg * 0.08
            self.time_since_damage = 0.0
            self.damage_flash = 0.45
            return

        self.player_health -= dmg
        self.time_since_damage = 0.0
        self.damage_flash = 0.45
        if self.player_health <= 0:
            self.player_downed = True
            self.player_bleed_out = 14.0
            self.player_health = 1.0

    def apply_damage_to_remote(self, remote: RemotePlayer, dmg: float, source_x: float, source_y: float) -> None:
        _ = source_x
        _ = source_y
        if remote.health <= 0 and not remote.downed:
            return
        if remote.downed:
            remote.bleed_out -= dmg * 0.08
            remote.time_since_damage = 0.0
            return

        remote.health -= dmg
        remote.time_since_damage = 0.0
        if remote.health <= 0:
            remote.downed = True
            remote.bleed_out = 14.0
            remote.health = 1.0

    def award_money(self, collector_id: str, amount: int) -> None:
        if amount <= 0:
            return
        if not self.shared_money:
            if collector_id == "host":
                self.player_money += amount
            else:
                remote = self.remote_players.get(collector_id)
                if remote is not None:
                    remote.money += amount
            return

        players: list[str] = ["host"] + list(self.remote_players.keys())
        share = max(1, amount // max(1, len(players)))
        remainder = amount - share * len(players)
        for i, pid in enumerate(players):
            add = share + (1 if i < remainder else 0)
            if pid == "host":
                self.player_money += add
            else:
                remote = self.remote_players.get(pid)
                if remote is not None:
                    remote.money += add

    def update_player_movement(self, dt: float) -> None:
        speed = 3.2 * self.get_move_speed_multiplier()
        if "shift_l" in self.keys or "shift_r" in self.keys:
            speed = 4.2 * self.get_move_speed_multiplier()

        move_x = 0.0
        move_y = 0.0
        sin_a = math.sin(self.player_angle)
        cos_a = math.cos(self.player_angle)

        if "w" in self.keys:
            move_x += cos_a * speed * dt
            move_y += sin_a * speed * dt
        if "s" in self.keys:
            move_x -= cos_a * speed * dt
            move_y -= sin_a * speed * dt
        if "a" in self.keys:
            move_x += math.cos(self.player_angle - math.pi / 2) * speed * dt
            move_y += math.sin(self.player_angle - math.pi / 2) * speed * dt
        if "d" in self.keys:
            move_x += math.cos(self.player_angle + math.pi / 2) * speed * dt
            move_y += math.sin(self.player_angle + math.pi / 2) * speed * dt

        if "left" in self.keys:
            self.player_angle -= 1.7 * dt
        if "right" in self.keys:
            self.player_angle += 1.7 * dt

        self.player_angle = normalize_angle(self.player_angle)
        self.try_move_player(move_x, move_y)

    def try_move_player(self, dx: float, dy: float) -> None:
        next_x = self.player_x + dx
        next_y = self.player_y + dy

        if self.can_move(next_x, self.player_y, PLAYER_RADIUS):
            self.player_x = next_x
        if self.can_move(self.player_x, next_y, PLAYER_RADIUS):
            self.player_y = next_y

    def choose_bot_target(self, bot: Bot) -> tuple[str, float, float] | None:
        candidates: list[tuple[str, float, float]] = []
        if self.player_health > 0 and not self.player_downed:
            candidates.append(("host", self.player_x, self.player_y))
        if self.net_mode == "host":
            for remote in self.remote_players.values():
                if remote.health > 0 and not remote.downed:
                    candidates.append((remote.player_id, remote.x, remote.y))

        if not candidates:
            return None

        return min(candidates, key=lambda item: distance(bot.x, bot.y, item[1], item[2]))

    def update_bots(self, dt: float) -> None:
        for bot in self.bots:
            if not bot.alive:
                continue

            target = self.choose_bot_target(bot)
            if target is None:
                continue
            target_id, target_x, target_y = target

            bot.ai_cooldown -= dt
            bot.fire_cooldown -= dt

            dist_to_player = distance(bot.x, bot.y, target_x, target_y)
            has_los = self.line_of_sight(bot.x, bot.y, target_x, target_y)

            if bot.ai_cooldown <= 0:
                self.assign_bot_tactic(bot, target_x, target_y, has_los, dist_to_player)
                bot.ai_cooldown = random.uniform(0.65, 1.3)

            self.move_bot_toward_target(bot, dt)

            if has_los and dist_to_player < bot.attack_range and bot.fire_cooldown <= 0:
                base_hit = 0.78 - dist_to_player * 0.055
                if bot.state == "cover":
                    base_hit += 0.08
                base_hit += bot.hit_bonus
                hit_chance = clamp(base_hit, 0.2, 0.84)

                if random.random() < hit_chance:
                    dmg = random.randint(bot.damage_min, bot.damage_max) + self.wave // 3
                    if target_id == "host":
                        self.apply_damage_to_host(dmg, bot.x, bot.y)
                    else:
                        remote = self.remote_players.get(target_id)
                        if remote is not None:
                            self.apply_damage_to_remote(remote, dmg, bot.x, bot.y)

                bot.fire_cooldown = random.uniform(0.45, 1.05)

    def assign_bot_tactic(self, bot: Bot, target_x: float, target_y: float, has_los: bool, dist_to_player: float) -> None:
        if bot.kind == "flanker":
            flank = self.pick_flank_for_bot(bot, target_x, target_y)
            bot.target_x, bot.target_y = flank
            bot.state = "flank"
            return
        if bot.kind == "sharpshooter" and has_los and dist_to_player > 5.0:
            cover = self.pick_cover_for_bot(bot, target_x, target_y)
            if cover:
                bot.target_x, bot.target_y = cover
                bot.state = "cover"
                return

        if has_los and dist_to_player < 8.8:
            if random.random() < 0.58:
                cover = self.pick_cover_for_bot(bot, target_x, target_y)
                if cover:
                    bot.target_x, bot.target_y = cover
                    bot.state = "cover"
                    return
            flank = self.pick_flank_for_bot(bot, target_x, target_y)
            bot.target_x, bot.target_y = flank
            bot.state = "flank"
            return

        if dist_to_player > 7.0:
            flank = self.pick_flank_for_bot(bot, target_x, target_y)
            bot.target_x, bot.target_y = flank
            bot.state = "flank"
        else:
            angle = random.uniform(0.0, math.tau)
            radius = random.uniform(1.8, 3.3)
            tx = target_x + math.cos(angle) * radius
            ty = target_y + math.sin(angle) * radius
            tx, ty = self.snap_to_free(tx, ty, target_x, target_y)
            bot.target_x, bot.target_y = tx, ty
            bot.state = "pressure"

    def pick_cover_for_bot(self, bot: Bot, target_x: float, target_y: float) -> tuple[float, float] | None:
        if not self.cover_points:
            return None

        sample_size = min(24, len(self.cover_points))
        sample = random.sample(self.cover_points, sample_size)
        best = None
        best_score = float("inf")

        for cx, cy in sample:
            dist_player = distance(cx, cy, target_x, target_y)
            if dist_player < 2.0 or dist_player > 10.0:
                continue
            dist_bot = distance(cx, cy, bot.x, bot.y)
            if dist_bot > 11.0:
                continue

            exposed = self.line_of_sight(target_x, target_y, cx, cy)
            score = dist_bot + (4.2 if exposed else 0.0)
            if score < best_score:
                best_score = score
                best = (cx, cy)

        return best

    def pick_flank_for_bot(self, bot: Bot, target_x: float, target_y: float) -> tuple[float, float]:
        angle_to_player = math.atan2(target_y - bot.y, target_x - bot.x)
        side = random.choice([-1, 1])
        flank_angle = angle_to_player + side * (math.pi / 2) + random.uniform(-0.42, 0.42)
        radius = random.uniform(3.1, 5.3)

        tx = target_x + math.cos(flank_angle) * radius
        ty = target_y + math.sin(flank_angle) * radius
        return self.snap_to_free(tx, ty, target_x, target_y)

    def snap_to_free(self, x: float, y: float, anchor_x: float | None = None, anchor_y: float | None = None) -> tuple[float, float]:
        if self.can_move(x, y, 0.24):
            return x, y

        if anchor_x is None:
            anchor_x = self.player_x
        if anchor_y is None:
            anchor_y = self.player_y

        best = (anchor_x, anchor_y)
        best_dist = float("inf")
        for _ in range(10):
            angle = random.uniform(0.0, math.tau)
            radius = random.uniform(2.0, 6.0)
            nx = anchor_x + math.cos(angle) * radius
            ny = anchor_y + math.sin(angle) * radius
            if not self.can_move(nx, ny, 0.24):
                continue
            d = distance(nx, ny, x, y)
            if d < best_dist:
                best = (nx, ny)
                best_dist = d

        return best

    def move_bot_toward_target(self, bot: Bot, dt: float) -> None:
        dx = bot.target_x - bot.x
        dy = bot.target_y - bot.y
        dist = math.hypot(dx, dy)
        if dist < 0.1:
            return

        speed = bot.speed
        if bot.state == "cover":
            speed *= 0.95
        elif bot.state == "flank":
            speed *= 1.1

        step = min(dist, speed * dt)
        mx = dx / dist * step
        my = dy / dist * step

        nx = bot.x + mx
        ny = bot.y + my

        if self.can_move(nx, bot.y, bot.radius):
            bot.x = nx
        if self.can_move(bot.x, ny, bot.radius):
            bot.y = ny

    def update_drops(self, dt: float) -> None:
        kept: list[MoneyDrop] = []
        for drop in self.money_drops:
            drop.ttl -= dt
            if drop.ttl <= 0:
                continue

            collector = None
            collector_dist = 999.0

            d_local = distance(drop.x, drop.y, self.player_x, self.player_y)
            if self.player_health > 0 and not self.player_downed and d_local < 0.56:
                collector = "host"
                collector_dist = d_local

            if self.net_mode == "host":
                for remote in self.remote_players.values():
                    if remote.health <= 0 or remote.downed:
                        continue
                    d_remote = distance(drop.x, drop.y, remote.x, remote.y)
                    if d_remote < 0.56 and d_remote < collector_dist:
                        collector = remote.player_id
                        collector_dist = d_remote

            if collector == "host":
                self.award_money("host", drop.value)
            elif isinstance(collector, str):
                self.award_money(collector, drop.value)
            else:
                kept.append(drop)

        self.money_drops = kept

    def handle_shooting(self, now: float) -> None:
        if self.net_mode == "client":
            return
        if self.pause_open:
            return
        if self.player_downed:
            return
        if self.player_health <= 0:
            return
        if self.shop_open:
            return
        if self.current_reload_weapon is not None:
            return
        if not self.mouse_down:
            return
        if now < self.next_fire_at:
            return

        weapon = self.current_weapon
        config = WEAPON_DATA[weapon]

        if not config["infinite"] and self.clip[weapon] <= 0:
            self.start_reload(now)
            if self.clip[weapon] <= 0:
                if self.ammo[weapon] <= 0 and weapon != "pistol":
                    self.current_weapon = "pistol"
                return

        if not config["infinite"] and self.ammo[weapon] <= 0 and self.clip[weapon] <= 0:
            if weapon != "pistol":
                self.current_weapon = "pistol"
            return

        self.next_fire_at = now + config["fire_rate"]
        self.weapon_kick = 1.0
        flash_scale = 1.0
        if weapon == "shotgun":
            flash_scale = 1.35
        elif weapon == "rpg":
            flash_scale = 1.8
        self.muzzle_flash_timer = max(self.muzzle_flash_timer, 0.12 * flash_scale)

        if not config["infinite"]:
            self.clip[weapon] = max(0, self.clip[weapon] - 1)

        if weapon == "rpg":
            self.game_state = "glitch"
            self.glitch_timer = 1.2
            return

        spread_mul = self.get_spread_multiplier()
        recoil_mul = self.get_recoil_multiplier()
        self.spread_heat = min(1.0, self.spread_heat + float(config.get("spread_growth", 0.08)))

        pellets = config["pellets"]
        for _ in range(pellets):
            recoil_pattern = RECOIL_PATTERNS.get(weapon, [0.0])
            recoil_offset = recoil_pattern[self.recoil_index[weapon] % len(recoil_pattern)]
            self.recoil_index[weapon] += 1
            shot_spread = config["spread"] * spread_mul * (1.0 + self.spread_heat * 0.8)
            recoil_offset *= float(config.get("recoil_scale", 1.0)) * recoil_mul
            shot_angle = self.player_angle + recoil_offset + random.uniform(-shot_spread, shot_spread)
            target, headshot = self.get_first_bot_hit(shot_angle, config["range"])
            if target is None:
                continue

            dmg = config["damage"] * self.get_weapon_damage_multiplier()
            if headshot:
                dmg *= 1.7
            target.health -= dmg
            if target.health <= 0 and target.alive:
                self.kill_bot(target)

        if not config["infinite"]:
            if self.clip[weapon] <= 0:
                self.start_reload(now)
            if self.clip[weapon] <= 0 and self.ammo[weapon] <= 0 and weapon != "pistol":
                self.current_weapon = "pistol"

    def get_first_bot_hit_from(
        self,
        origin_x: float,
        origin_y: float,
        shot_angle: float,
        max_range: float,
    ) -> tuple[Bot | None, bool]:
        cos_a = math.cos(shot_angle)
        sin_a = math.sin(shot_angle)

        closest: Bot | None = None
        headshot = False
        closest_dist = max_range + 1.0

        for bot in self.bots:
            if not bot.alive:
                continue

            dx = bot.x - origin_x
            dy = bot.y - origin_y
            along = dx * cos_a + dy * sin_a
            if along <= 0 or along > max_range:
                continue

            perp = abs(-sin_a * dx + cos_a * dy)
            if perp > bot.radius:
                continue

            if along < closest_dist and self.line_of_sight(origin_x, origin_y, bot.x, bot.y):
                closest = bot
                headshot = perp <= bot.radius * 0.4
                closest_dist = along

        return closest, headshot

    def get_first_bot_hit(self, shot_angle: float, max_range: float) -> tuple[Bot | None, bool]:
        return self.get_first_bot_hit_from(self.player_x, self.player_y, shot_angle, max_range)

    def kill_bot(self, bot: Bot) -> None:
        bot.alive = False
        self.gain_xp(int(10 + self.wave * 0.8 + bot.money_multiplier * 4))
        money_count = 1 if random.random() < 0.75 else 2
        for _ in range(money_count):
            value = int((random.randint(28, 62) + self.wave * 4) * bot.money_multiplier)
            ox = random.uniform(-0.16, 0.16)
            oy = random.uniform(-0.16, 0.16)
            self.money_drops.append(MoneyDrop(bot.x + ox, bot.y + oy, value))

    def spawn_wave(self) -> None:
        self.wave += 1
        spawn_count = min(4 + self.wave * 2, 24)
        self.wave_cleared_award_pending = True

        if self.player_health <= 0 and not self.player_downed:
            self.player_health = 65.0 + self.perks["vitality"] * 3
            self.player_x, self.player_y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 4.0)
        else:
            self.player_health = min(self.get_max_health_cap(), self.player_health + 12.0)
        self.player_downed = False
        self.player_bleed_out = 0.0
        self.player_revive_progress = 0.0

        if self.net_mode == "host":
            for remote in self.remote_players.values():
                if remote.health <= 0 and not remote.downed:
                    remote.health = 65.0
                    remote.x, remote.y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 4.0)
                else:
                    remote.health = min(100.0, remote.health + 12.0)
                remote.downed = False
                remote.bleed_out = 0.0
                remote.revive_progress = 0.0

        reachable_cells = self.get_reachable_floor_cells()

        if self.wave % 4 == 0:
            self.objective_type = "defend_zone"
            self.objective_timer = min(22.0, 11.0 + self.wave * 0.55)
            zx, zy = self.pick_spawn_far_from_player(reachable_cells)
            self.objective_zone = (zx, zy, 2.4)
        else:
            self.objective_type = "eliminate"
            self.objective_timer = 0.0
            self.objective_zone = None

        if self.wave > 1:
            self.gain_xp(14 + self.wave * 2)

        for _ in range(spawn_count):
            x, y = self.pick_spawn_far_from_player(reachable_cells)
            self.bots.append(self.make_wave_bot(x, y))

        if self.wave % 5 == 0:
            x, y = self.pick_spawn_far_from_player(reachable_cells)
            self.bots.append(self.make_wave_bot(x, y, forced_kind="boss"))

    def make_wave_bot(self, x: float, y: float, forced_kind: str | None = None) -> Bot:
        kind = forced_kind
        if kind is None:
            roll = random.random()
            if roll < 0.56:
                kind = "grunt"
            elif roll < 0.76:
                kind = "flanker"
            elif roll < 0.92:
                kind = "sharpshooter"
            else:
                kind = "tank"

        arch = BOT_ARCHETYPES[kind]
        bot_hp = (65 + self.wave * 7) * float(arch["hp_mult"])
        bot_speed = (1.2 + min(0.6, self.wave * 0.04)) * float(arch["speed_mult"])
        return Bot(
            x=x,
            y=y,
            health=bot_hp,
            speed=bot_speed,
            kind=kind,
            target_x=x,
            target_y=y,
            attack_range=float(arch["attack_range"]),
            hit_bonus=float(arch["hit_bonus"]),
            damage_min=max(1, 4 + int(arch["damage_min_bonus"])),
            damage_max=max(2, 9 + int(arch["damage_max_bonus"])),
            money_multiplier=float(arch["money_mult"]),
        )

    def pick_spawn_far_from_player(self, spawn_cells: list[tuple[int, int]]) -> tuple[float, float]:
        candidates: list[tuple[float, float, float]] = []
        for cell_x, cell_y in spawn_cells:
            x = cell_x + 0.5
            y = cell_y + 0.5
            if not self.can_move(x, y, 0.24):
                continue

            blocked = any(distance(x, y, b.x, b.y) < 0.8 for b in self.bots if b.alive)
            if blocked:
                continue

            dist = distance(x, y, self.player_x, self.player_y)
            candidates.append((x, y, dist))

        if not candidates:
            relaxed: list[tuple[float, float, float]] = []
            for cell_x, cell_y in spawn_cells:
                x = cell_x + 0.5
                y = cell_y + 0.5
                if not self.can_move(x, y, 0.24):
                    continue
                blocked = any(distance(x, y, b.x, b.y) < 0.35 for b in self.bots if b.alive)
                if blocked:
                    continue
                relaxed.append((x, y, distance(x, y, self.player_x, self.player_y)))

            if relaxed:
                far_relaxed = [item for item in relaxed if item[2] >= 6.5]
                if far_relaxed:
                    x, y, _ = random.choice(far_relaxed)
                    return x, y
                x, y, _ = max(relaxed, key=lambda item: item[2])
                return x, y

            fallback: list[tuple[float, float, float]] = []
            for cell_x, cell_y in spawn_cells:
                x = cell_x + 0.5
                y = cell_y + 0.5
                if self.can_move(x, y, 0.24):
                    fallback.append((x, y, distance(x, y, self.player_x, self.player_y)))
            if fallback:
                x, y, _ = max(fallback, key=lambda item: item[2])
                return x, y
            return self.player_x, self.player_y

        far_candidates = [item for item in candidates if item[2] >= 6.5]
        if far_candidates:
            x, y, _ = random.choice(far_candidates)
            return x, y

        # If the reachable area is small, spawn as far away as possible within it.
        x, y, _ = max(candidates, key=lambda item: item[2])
        return x, y

    def alive_bots(self) -> int:
        return sum(1 for bot in self.bots if bot.alive)

    def is_wall(self, x: float, y: float) -> bool:
        ix = int(x)
        iy = int(y)
        if iy < 0 or iy >= len(WORLD_MAP) or ix < 0 or ix >= len(WORLD_MAP[0]):
            return True
        return WORLD_MAP[iy][ix] == "#"

    def can_move(self, x: float, y: float, radius: float) -> bool:
        tests = [
            (x - radius, y - radius),
            (x + radius, y - radius),
            (x - radius, y + radius),
            (x + radius, y + radius),
        ]
        for px, py in tests:
            if self.is_wall(px, py):
                return False
        return True

    def line_of_sight(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        dx = x2 - x1
        dy = y2 - y1
        dist = math.hypot(dx, dy)
        if dist < 0.01:
            return True

        steps = max(1, int(dist / 0.08))
        for i in range(1, steps):
            t = i / steps
            x = x1 + dx * t
            y = y1 + dy * t
            if self.is_wall(x, y):
                return False
        return True

    def cast_ray(self, angle: float) -> tuple[float, int]:
        px = self.player_x
        py = self.player_y
        sin_a = math.sin(angle)
        cos_a = math.cos(angle)

        map_x = int(px)
        map_y = int(py)

        delta_dist_x = abs(1.0 / cos_a) if abs(cos_a) > 1e-8 else 1e6
        delta_dist_y = abs(1.0 / sin_a) if abs(sin_a) > 1e-8 else 1e6

        if cos_a < 0:
            step_x = -1
            side_dist_x = (px - map_x) * delta_dist_x
        else:
            step_x = 1
            side_dist_x = (map_x + 1.0 - px) * delta_dist_x

        if sin_a < 0:
            step_y = -1
            side_dist_y = (py - map_y) * delta_dist_y
        else:
            step_y = 1
            side_dist_y = (map_y + 1.0 - py) * delta_dist_y

        side = 0
        dist = MAX_DEPTH

        for _ in range(160):
            if side_dist_x < side_dist_y:
                map_x += step_x
                dist = side_dist_x
                side_dist_x += delta_dist_x
                side = 0
            else:
                map_y += step_y
                dist = side_dist_y
                side_dist_y += delta_dist_y
                side = 1

            if dist > MAX_DEPTH:
                return MAX_DEPTH, side

            if map_y < 0 or map_y >= len(WORLD_MAP) or map_x < 0 or map_x >= len(WORLD_MAP[0]):
                return MAX_DEPTH, side

            if WORLD_MAP[map_y][map_x] == "#":
                return dist, side

        return MAX_DEPTH, side

    def shop_slot_from_mouse(self) -> int | None:
        cx = WIDTH // 2
        cy = HALF_HEIGHT
        dx = self.last_mouse_x - cx
        dy = self.last_mouse_y - cy
        radius = math.hypot(dx, dy)

        if radius < 60 or radius > 230:
            return None

        theta = math.atan2(dy, dx)
        if theta < 0:
            theta += math.tau

        slot = int(theta / (math.tau / 4.0))
        return slot

    def render(self, now: float) -> None:
        self.canvas.delete("all")
        self.pause_hitboxes = []

        if self.game_state == "bsod":
            self.render_bsod(now)
            return

        self.render_world()
        self.render_sprites()
        self.render_viewmodel(now)
        self.render_hud()

        if self.game_state == "glitch":
            self.render_glitch_overlay()
        elif self.game_state == "dead":
            self.render_dead_overlay()

        if self.pause_open:
            self.render_pause_menu()

    def render_world(self) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HALF_HEIGHT, fill="#2a2e36", outline="")
        self.canvas.create_rectangle(0, HALF_HEIGHT, WIDTH, HEIGHT, fill="#181614", outline="")

        slice_width = WIDTH / RAY_COUNT
        self.zbuffer: list[float] = []

        for i in range(RAY_COUNT):
            ray_angle = self.player_angle - FOV / 2 + (i / RAY_COUNT) * FOV
            dist, side = self.cast_ray(ray_angle)

            corrected = dist * math.cos(ray_angle - self.player_angle)
            corrected = max(0.0001, corrected)
            self.zbuffer.append(corrected)

            proj_height = int((HEIGHT * 0.95) / corrected)
            proj_height = min(HEIGHT, proj_height)

            shade = int(230 - corrected * 20)
            shade = int(clamp(shade, 24, 230))
            if side == 1:
                shade = int(shade * 0.72)

            color = rgb(shade, shade, shade + 5)
            x1 = i * slice_width
            x2 = x1 + slice_width + 1
            y1 = HALF_HEIGHT - proj_height // 2
            y2 = HALF_HEIGHT + proj_height // 2
            self.canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

    def render_sprites(self) -> None:
        items: list[tuple[float, str, object]] = []

        for bot in self.bots:
            if bot.alive:
                d = distance(bot.x, bot.y, self.player_x, self.player_y)
                items.append((d, "bot", bot))

        for drop in self.money_drops:
            d = distance(drop.x, drop.y, self.player_x, self.player_y)
            items.append((d, "money", drop))

        if self.team_ping is not None:
            d = distance(self.team_ping[0], self.team_ping[1], self.player_x, self.player_y)
            items.append((d, "ping", {"x": self.team_ping[0], "y": self.team_ping[1], "owner": self.team_ping[3]}))

        if self.objective_type == "defend_zone" and self.objective_zone is not None:
            d = distance(self.objective_zone[0], self.objective_zone[1], self.player_x, self.player_y)
            items.append((d, "objective", {"x": self.objective_zone[0], "y": self.objective_zone[1]}))

        teammates: list[TeammateView] = []
        if self.net_mode == "host":
            for remote in self.remote_players.values():
                teammates.append(
                    TeammateView(
                        player_id=remote.player_id,
                        name=remote.name,
                        x=remote.x,
                        y=remote.y,
                        angle=remote.angle,
                        health=remote.health,
                        weapon=remote.current_weapon,
                        downed=remote.downed,
                    )
                )
        else:
            teammates = list(self.remote_render_players)

        for teammate in teammates:
            d = distance(teammate.x, teammate.y, self.player_x, self.player_y)
            if d < 0.35:
                continue
            items.append((d, "human", teammate))

        items.sort(key=lambda item: item[0], reverse=True)

        for dist, kind, obj in items:
            obj_x = obj.x if hasattr(obj, "x") else float(obj["x"])
            obj_y = obj.y if hasattr(obj, "y") else float(obj["y"])
            dx = obj_x - self.player_x
            dy = obj_y - self.player_y
            theta = normalize_angle(math.atan2(dy, dx) - self.player_angle)

            if theta > math.pi:
                theta -= math.tau

            if abs(theta) > FOV * 0.58:
                continue

            screen_x = (0.5 + theta / FOV) * WIDTH
            col = int((screen_x / WIDTH) * RAY_COUNT)
            if col < 0 or col >= len(self.zbuffer):
                continue

            if dist > self.zbuffer[col] + 0.12:
                continue

            if kind == "bot":
                bot = obj
                size_scale = 1.0
                if bot.kind == "tank":
                    size_scale = 1.18
                elif bot.kind == "boss":
                    size_scale = 1.34
                elif bot.kind == "flanker":
                    size_scale = 0.92
                h = int((HEIGHT * 0.72 * size_scale) / max(0.15, dist))
                w = int(h * 0.48)
                x1 = screen_x - w / 2
                y1 = HALF_HEIGHT - h / 2
                x2 = screen_x + w / 2
                y2 = HALF_HEIGHT + h / 2

                body = "#d64a4a"
                if bot.kind == "flanker":
                    body = "#dc8750"
                elif bot.kind == "tank":
                    body = "#7b5ad0"
                elif bot.kind == "sharpshooter":
                    body = "#49a2d6"
                elif bot.kind == "boss":
                    body = "#f04d9d"
                if bot.state == "cover":
                    body = "#c28a3e"
                self.canvas.create_rectangle(x1, y1, x2, y2, fill=body, outline="")
                head_h = h * 0.28
                self.canvas.create_oval(x1 + w * 0.2, y1 - head_h * 0.6, x2 - w * 0.2, y1 + head_h * 0.7, fill="#e4b7a0", outline="")
            elif kind == "human":
                teammate = obj
                h = int((HEIGHT * 0.7) / max(0.15, dist))
                w = int(h * 0.46)
                x1 = screen_x - w / 2
                y1 = HALF_HEIGHT - h / 2
                x2 = screen_x + w / 2
                y2 = HALF_HEIGHT + h / 2

                downed = teammate.downed or teammate.health <= 0
                body_color = "#4a8ad6" if not downed else "#5a5a5a"
                name_color = "#bcd8ff" if not downed else "#c8c8c8"
                label = teammate.name if not downed else f"{teammate.name} [DOWN]"

                self.canvas.create_rectangle(x1, y1, x2, y2, fill=body_color, outline="")
                head_h = h * 0.28
                head_color = "#f1c7ac" if not downed else "#b3b3b3"
                self.canvas.create_oval(x1 + w * 0.2, y1 - head_h * 0.6, x2 - w * 0.2, y1 + head_h * 0.7, fill=head_color, outline="")
                self.canvas.create_text(screen_x, y1 - 14, text=label, fill=name_color, font=("Consolas", 10, "bold"))
            elif kind == "money":
                h = int((HEIGHT * 0.22) / max(0.2, dist))
                w = h
                x1 = screen_x - w / 2
                y1 = HALF_HEIGHT + h * 0.2
                x2 = screen_x + w / 2
                y2 = y1 + h
                self.canvas.create_oval(x1, y1, x2, y2, fill="#68d96f", outline="")
            elif kind == "ping":
                size = int((HEIGHT * 0.16) / max(0.2, dist))
                self.canvas.create_oval(
                    screen_x - size,
                    HALF_HEIGHT - size,
                    screen_x + size,
                    HALF_HEIGHT + size,
                    outline="#ffd967",
                    width=3,
                )
                self.canvas.create_text(screen_x, HALF_HEIGHT - size - 14, text="PING", fill="#ffea95", font=("Consolas", 10, "bold"))
            elif kind == "objective":
                size = int((HEIGHT * 0.2) / max(0.2, dist))
                self.canvas.create_rectangle(
                    screen_x - size,
                    HALF_HEIGHT - size,
                    screen_x + size,
                    HALF_HEIGHT + size,
                    outline="#7ce6ff",
                    width=3,
                )
                self.canvas.create_text(screen_x, HALF_HEIGHT - size - 14, text="ZONE", fill="#b9f1ff", font=("Consolas", 10, "bold"))

    def render_viewmodel(self, now: float) -> None:
        if self.game_state not in {"playing", "glitch"}:
            return

        moving = any(key in self.keys for key in ("w", "a", "s", "d"))
        bob = math.sin(now * 8.0) * (6.0 if moving else 2.2)
        sway = math.cos(now * 6.4) * (4.0 if moving else 1.4)
        kick = self.weapon_kick * 16.0

        base_x = WIDTH - 320 + sway
        base_y = HEIGHT - 170 + bob + kick * 0.55

        hand_x1 = base_x + 120
        hand_y1 = base_y + 92
        hand_x2 = base_x + 258
        hand_y2 = base_y + 172
        self.canvas.create_rectangle(hand_x1, hand_y1, hand_x2, hand_y2, fill="#313949", outline="")
        self.canvas.create_oval(hand_x1 + 16, hand_y1 - 10, hand_x2, hand_y2 + 12, fill="#ddb195", outline="")

        weapon = self.current_weapon
        muzzle_x = base_x + 280
        muzzle_y = base_y + 66

        if weapon == "pistol":
            self.canvas.create_rectangle(base_x + 40, base_y + 56, base_x + 210, base_y + 96, fill="#2f353d", outline="")
            self.canvas.create_rectangle(base_x + 182, base_y + 63, base_x + 276, base_y + 83, fill="#4a525d", outline="")
            self.canvas.create_polygon(
                base_x + 95,
                base_y + 95,
                base_x + 155,
                base_y + 95,
                base_x + 145,
                base_y + 164,
                base_x + 92,
                base_y + 164,
                fill="#2a3038",
                outline="",
            )
            muzzle_x = base_x + 280
            muzzle_y = base_y + 73
        elif weapon == "shotgun":
            self.canvas.create_rectangle(base_x + 20, base_y + 68, base_x + 285, base_y + 96, fill="#53422e", outline="")
            self.canvas.create_rectangle(base_x + 130, base_y + 56, base_x + 316, base_y + 78, fill="#767d85", outline="")
            self.canvas.create_rectangle(base_x + 96, base_y + 96, base_x + 150, base_y + 124, fill="#343a42", outline="")
            muzzle_x = base_x + 318
            muzzle_y = base_y + 68
        elif weapon == "rifle":
            self.canvas.create_rectangle(base_x + 24, base_y + 70, base_x + 290, base_y + 104, fill="#2d3f2f", outline="")
            self.canvas.create_rectangle(base_x + 170, base_y + 62, base_x + 326, base_y + 82, fill="#4f5a63", outline="")
            self.canvas.create_rectangle(base_x + 106, base_y + 102, base_x + 146, base_y + 154, fill="#1f2429", outline="")
            self.canvas.create_rectangle(base_x + 76, base_y + 60, base_x + 124, base_y + 70, fill="#606870", outline="")
            muzzle_x = base_x + 328
            muzzle_y = base_y + 72
        else:
            self.canvas.create_rectangle(base_x + 16, base_y + 66, base_x + 302, base_y + 108, fill="#4a535f", outline="")
            self.canvas.create_oval(base_x + 272, base_y + 64, base_x + 350, base_y + 112, fill="#5f6975", outline="")
            self.canvas.create_polygon(
                base_x + 338,
                base_y + 71,
                base_x + 382,
                base_y + 87,
                base_x + 338,
                base_y + 103,
                fill="#c9b05d",
                outline="",
            )
            self.canvas.create_rectangle(base_x + 78, base_y + 108, base_x + 114, base_y + 156, fill="#2e343b", outline="")
            muzzle_x = base_x + 378
            muzzle_y = base_y + 87

        if self.muzzle_flash_timer > 0:
            self.render_muzzle_flash(muzzle_x, muzzle_y, weapon)

    def render_muzzle_flash(self, x: float, y: float, weapon: str) -> None:
        size = 24.0 + self.muzzle_flash_timer * 42.0
        if weapon == "shotgun":
            size *= 1.2
        elif weapon == "rpg":
            size *= 1.6

        self.canvas.create_oval(x - size, y - size, x + size, y + size, fill="#ffd56b", outline="")
        self.canvas.create_oval(x - size * 0.62, y - size * 0.62, x + size * 0.62, y + size * 0.62, fill="#fff0b6", outline="")

        rays = 8
        for i in range(rays):
            ang = (math.tau / rays) * i + random.uniform(-0.18, 0.18)
            length = size * random.uniform(1.15, 1.65)
            rx = x + math.cos(ang) * length
            ry = y + math.sin(ang) * length
            self.canvas.create_line(x, y, rx, ry, fill="#fff7d0", width=3)

    def render_hud(self) -> None:
        self.canvas.create_line(WIDTH // 2 - 10, HALF_HEIGHT, WIDTH // 2 + 10, HALF_HEIGHT, fill="#f4f4f4", width=2)
        self.canvas.create_line(WIDTH // 2, HALF_HEIGHT - 10, WIDTH // 2, HALF_HEIGHT + 10, fill="#f4f4f4", width=2)

        self.canvas.create_rectangle(24, 24, 300, 56, fill="#000", outline="#343434", width=2)
        hp_cap = max(1.0, self.get_max_health_cap())
        hp_width = int(272 * (self.player_health / hp_cap))
        hp_color = "#52cc52" if self.player_health > 35 else "#cc4a3f"
        self.canvas.create_rectangle(26, 26, 26 + hp_width, 54, fill=hp_color, outline="")
        self.canvas.create_text(162, 40, text=f"Health: {int(self.player_health)}", fill="#fff", font=("Consolas", 14, "bold"))
        if self.player_downed:
            self.canvas.create_text(162, 60, text=f"DOWNED {self.player_bleed_out:.1f}s", fill="#ff9f9f", font=("Consolas", 10, "bold"))

        if WEAPON_DATA[self.current_weapon]["infinite"]:
            ammo_text = "INF"
        else:
            ammo_text = f"{self.clip[self.current_weapon]}/{self.ammo[self.current_weapon]}"
        weapon_name = WEAPON_DATA[self.current_weapon]["name"]
        self.canvas.create_text(26, 80, anchor="nw", text=f"Weapon: {weapon_name}", fill="#f3f3f3", font=("Consolas", 18, "bold"))
        self.canvas.create_text(26, 108, anchor="nw", text=f"Ammo: {ammo_text}", fill="#f3f3f3", font=("Consolas", 16))

        if self.current_reload_weapon is not None:
            self.canvas.create_text(26, 128, anchor="nw", text="RELOADING...", fill="#ffcf7a", font=("Consolas", 12, "bold"))
        elif not WEAPON_DATA[self.current_weapon]["infinite"]:
            low = self.clip[self.current_weapon] <= max(2, int(WEAPON_DATA[self.current_weapon]["mag_size"] * 0.2))
            if low:
                self.canvas.create_text(26, 128, anchor="nw", text="LOW AMMO", fill="#ff6b6b", font=("Consolas", 12, "bold"))

        self.canvas.create_text(26, 138, anchor="nw", text=f"Money: ${self.player_money}", fill="#78e088", font=("Consolas", 18, "bold"))
        self.canvas.create_text(26, 166, anchor="nw", text=f"Wave: {self.wave}", fill="#dfdfdf", font=("Consolas", 16))
        self.canvas.create_text(26, 192, anchor="nw", text=f"Bots Alive: {self.alive_bots()}", fill="#dfdfdf", font=("Consolas", 16))
        self.canvas.create_text(
            26,
            218,
            anchor="nw",
            text=f"Level {self.profile_level}  XP {self.profile_xp}/{self.xp_to_next_level()}  Perks {self.perk_points}",
            fill="#c8e0ff",
            font=("Consolas", 12, "bold"),
        )

        teammate_count = len(self.remote_players) if self.net_mode == "host" else len(self.remote_render_players)
        if self.net_mode != "single":
            self.canvas.create_text(26, 242, anchor="nw", text=f"Teammates: {teammate_count}", fill="#9cc9ff", font=("Consolas", 16))
            self.canvas.create_text(26, 268, anchor="nw", text=self.net_status, fill="#9cc9ff", font=("Consolas", 12))

        objective_text = "Objective: Eliminate all bots"
        if self.objective_type == "defend_zone":
            objective_text = f"Objective: Hold zone {self.objective_timer:.1f}s"
        self.canvas.create_text(
            26,
            HEIGHT - 108,
            anchor="nw",
            text=objective_text,
            fill="#bfe9ff",
            font=("Consolas", 12, "bold"),
        )
        if self.wave_timer > 0:
            self.canvas.create_text(
                26,
                HEIGHT - 128,
                anchor="nw",
                text=f"Next wave in {self.wave_timer:.1f}s",
                fill="#ffe8a1",
                font=("Consolas", 12, "bold"),
            )

        help_text = "WASD + Mouse | B Shop | Q Ping | R Reload | F1-4 Spend Perks"
        if self.net_mode == "client":
            help_text = "CO-OP Client | WASD+Mouse -> host | Q Ping | R Reload | Esc Settings"
        elif self.net_mode == "host":
            money_mode = "Shared $" if self.shared_money else "Split $"
            help_text = f"CO-OP Host | {money_mode} | Esc Settings | Others can join via your IP + port"

        self.canvas.create_text(
            WIDTH - 22,
            24,
            anchor="ne",
            text=help_text,
            fill="#e0e0e0",
            font=("Consolas", 12),
        )

        self.canvas.create_text(
            WIDTH - 22,
            44,
            anchor="ne",
            text=f"Quality: {RAY_COUNT} rays ({'Auto' if self.adaptive_quality_enabled else 'Manual'})",
            fill="#cfd5de",
            font=("Consolas", 11),
        )

        if self.damage_direction_timer > 0.01:
            rel = normalize_angle(self.last_damage_from - self.player_angle)
            if rel > math.pi:
                rel -= math.tau
            arrow_radius = 42
            ax = WIDTH // 2 + math.cos(rel) * arrow_radius
            ay = HALF_HEIGHT + math.sin(rel) * arrow_radius
            bx = WIDTH // 2 + math.cos(rel) * (arrow_radius + 16)
            by = HALF_HEIGHT + math.sin(rel) * (arrow_radius + 16)
            self.canvas.create_line(ax, ay, bx, by, fill="#ff8a8a", width=3, arrow=tk.LAST)

        self.draw_weapon_bar()

        if self.shop_open and self.game_state == "playing":
            self.render_shop_wheel()

        if self.damage_flash > 0:
            alpha = int(clamp(self.damage_flash * 120, 0, 120))
            color = rgb(110 + alpha, 18, 18)
            self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill=color, outline="", stipple="gray50")

    def render_pause_menu(self) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#080b12", outline="", stipple="gray50")

        panel_w = min(860, WIDTH - 60)
        panel_h = min(520, HEIGHT - 60)
        x1 = (WIDTH - panel_w) // 2
        y1 = (HEIGHT - panel_h) // 2
        x2 = x1 + panel_w
        y2 = y1 + panel_h
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#151c29", outline="#6f88a8", width=3)

        self.canvas.create_text(
            x1 + 28,
            y1 + 22,
            anchor="nw",
            text="PAUSED | SETTINGS",
            fill="#edf4ff",
            font=("Consolas", 26, "bold"),
        )
        self.canvas.create_text(
            x1 + 30,
            y1 + 64,
            anchor="nw",
            text="Adjust settings with mouse clicks. Press Esc to resume.",
            fill="#c6d3e5",
            font=("Consolas", 12),
        )

        row_y = y1 + 94
        row_gap = 52
        res_w, res_h = self.available_resolutions[self.resolution_index]

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Mouse Sensitivity",
            f"{self.mouse_sensitivity:.4f}",
            "sens_down",
            "sens_up",
        )
        row_y += row_gap

        smoothing_text = "On" if self.mouse_smoothing_enabled else "Off"
        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Mouse Smoothing",
            smoothing_text,
            "smoothing",
            "smoothing",
            left_label="Toggle",
            right_label="Toggle",
        )
        row_y += row_gap

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Shared Team Money",
            "On" if self.shared_money else "Off",
            "shared_money",
            "shared_money",
            left_label="Toggle",
            right_label="Toggle",
        )
        row_y += row_gap

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Adaptive Quality",
            "On" if self.adaptive_quality_enabled else "Off",
            "adaptive_quality",
            "adaptive_quality",
            left_label="Toggle",
            right_label="Toggle",
        )
        row_y += row_gap

        fullscreen_text = "On" if self.fullscreen_enabled else "Off"
        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Fullscreen",
            fullscreen_text,
            "fullscreen",
            "fullscreen",
            left_label="Toggle",
            right_label="Toggle",
        )
        row_y += row_gap

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Resolution",
            f"{res_w} x {res_h}",
            "res_prev",
            "res_next",
        )
        row_y += row_gap

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "Field of View",
            f"{self.fov_degrees} deg",
            "fov_down",
            "fov_up",
        )
        row_y += row_gap

        self.draw_pause_adjust_row(
            x1,
            x2,
            row_y,
            "FPS Cap",
            str(self.fps_limit),
            "fps_down",
            "fps_up",
        )

        self.canvas.create_text(
            x1 + 30,
            y2 - 146,
            anchor="nw",
            text=f"Perks: F1 Vitality {self.perks['vitality']} | F2 Mobility {self.perks['mobility']} | "
            f"F3 Regen {self.perks['regen']} | F4 Weapon {self.perks['weapon']} | Points: {self.perk_points}",
            fill="#d2e5ff",
            font=("Consolas", 10),
        )

        if self.fullscreen_enabled:
            self.canvas.create_text(
                x1 + 30,
                y2 - 118,
                anchor="nw",
                text="Resolution preset applies when fullscreen is Off.",
                fill="#c6d3e5",
                font=("Consolas", 11),
            )

        button_y1 = y2 - 78
        button_y2 = y2 - 30
        button_w = 170
        gap = 24
        start_x = x2 - (button_w * 3 + gap * 2) - 28
        self.draw_pause_button(start_x, button_y1, start_x + button_w, button_y2, "Defaults", "defaults", "#34506d")
        self.draw_pause_button(
            start_x + button_w + gap,
            button_y1,
            start_x + button_w * 2 + gap,
            button_y2,
            "Resume",
            "resume",
            "#2f6d4f",
        )
        self.draw_pause_button(
            start_x + button_w * 2 + gap * 2,
            button_y1,
            start_x + button_w * 3 + gap * 2,
            button_y2,
            "Quit",
            "quit",
            "#7a3737",
        )

    def draw_pause_adjust_row(
        self,
        panel_x1: int,
        panel_x2: int,
        y: int,
        label: str,
        value: str,
        left_action: str,
        right_action: str,
        left_label: str = "-",
        right_label: str = "+",
    ) -> None:
        self.canvas.create_text(panel_x1 + 30, y + 9, anchor="nw", text=label, fill="#ecf3ff", font=("Consolas", 14, "bold"))

        control_x = panel_x2 - 310
        left_x1 = control_x
        left_x2 = control_x + 88
        right_x1 = control_x + 206
        right_x2 = control_x + 294

        self.draw_pause_button(left_x1, y, left_x2, y + 40, left_label, left_action, "#2a3442")
        self.canvas.create_rectangle(control_x + 94, y, control_x + 200, y + 40, fill="#0f1520", outline="#596c86", width=2)
        self.canvas.create_text(control_x + 147, y + 20, text=value, fill="#f7fbff", font=("Consolas", 12, "bold"))
        self.draw_pause_button(right_x1, y, right_x2, y + 40, right_label, right_action, "#2a3442")

    def draw_pause_button(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        text: str,
        action: str,
        fill: str,
    ) -> None:
        self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline="#8ca3c3", width=2)
        self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill="#eef6ff", font=("Consolas", 11, "bold"))
        self.pause_hitboxes.append(PauseHitbox(x1=x1, y1=y1, x2=x2, y2=y2, action=action))

    def draw_weapon_bar(self) -> None:
        y = HEIGHT - 74
        x = 26
        for weapon in WEAPON_ORDER:
            owned = self.owned_weapons[weapon]
            current = weapon == self.current_weapon
            base_color = "#2a2a2a" if owned else "#1a1a1a"
            border = "#f0e06a" if current else "#555"
            self.canvas.create_rectangle(x, y, x + 200, y + 44, fill=base_color, outline=border, width=2)

            if owned:
                extra = ""
                if not WEAPON_DATA[weapon]["infinite"]:
                    extra = f" ({self.clip[weapon]}/{self.ammo[weapon]})"
                label = f"{WEAPON_DATA[weapon]['name']}{extra}"
                color = "#f4f4f4"
            else:
                label = f"{WEAPON_DATA[weapon]['name']} ${WEAPON_DATA[weapon]['cost']}"
                color = "#8e8e8e"

            self.canvas.create_text(x + 100, y + 22, text=label, fill=color, font=("Consolas", 11, "bold"))
            x += 214

    def render_shop_wheel(self) -> None:
        cx = WIDTH // 2
        cy = HALF_HEIGHT
        slot = self.shop_slot_from_mouse()

        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#000", outline="", stipple="gray50")

        outer_r = 210
        inner_r = 70
        colors = ["#6b6f8f", "#8f6b6b", "#6f8f6b", "#8f834d"]

        for i, weapon in enumerate(WEAPON_ORDER):
            start = i * 90
            extent = 90
            fill = colors[i]
            if slot == i:
                fill = "#c6b66f"

            self.canvas.create_arc(
                cx - outer_r,
                cy - outer_r,
                cx + outer_r,
                cy + outer_r,
                start=-start,
                extent=-extent,
                fill=fill,
                outline="#1a1a1a",
                width=2,
                style=tk.PIESLICE,
            )

            mid_angle = math.radians(start + 45)
            tx = cx + math.cos(-mid_angle) * 145
            ty = cy + math.sin(-mid_angle) * 145

            owned = self.owned_weapons[weapon]
            if owned:
                txt = WEAPON_DATA[weapon]["name"]
                if weapon == self.current_weapon:
                    txt += "\n[EQUIPPED]"
                elif not WEAPON_DATA[weapon]["infinite"]:
                    txt += f"\nAmmo: {self.clip[weapon]}/{self.ammo[weapon]}"
            else:
                txt = f"{WEAPON_DATA[weapon]['name']}\n${WEAPON_DATA[weapon]['cost']}"

            self.canvas.create_text(tx, ty, text=txt, fill="#fff", font=("Consolas", 12, "bold"), justify="center")

        self.canvas.create_oval(cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r, fill="#101018", outline="#ddd", width=2)
        self.canvas.create_text(cx, cy - 8, text="SHOP", fill="#fff", font=("Consolas", 16, "bold"))
        self.canvas.create_text(cx, cy + 14, text="Click to buy/equip", fill="#ddd", font=("Consolas", 10))

    def render_glitch_overlay(self) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#091327", outline="", stipple="gray50")

        for _ in range(18):
            y = random.randint(0, HEIGHT)
            offset = random.randint(-80, 80)
            self.canvas.create_line(0 + offset, y, WIDTH + offset, y + random.randint(-8, 8), fill="#69b6ff", width=2)

        self.canvas.create_text(
            WIDTH // 2,
            HALF_HEIGHT,
            text="RPG payload destabilized simulation...",
            fill="#cfe6ff",
            font=("Consolas", 28, "bold"),
        )

    def render_dead_overlay(self) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#2a0000", outline="", stipple="gray50")
        self.canvas.create_text(WIDTH // 2, HALF_HEIGHT - 24, text="YOU DIED", fill="#ffd5d5", font=("Consolas", 58, "bold"))
        self.canvas.create_text(
            WIDTH // 2,
            HALF_HEIGHT + 34,
            text="Press R to restart the simulation",
            fill="#ffe8e8",
            font=("Consolas", 18),
        )

    def render_bsod(self, now: float) -> None:
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill="#0a2ea8", outline="")

        elapsed = now - self.bsod_started_at
        lines = [
            "A problem has been detected and Windows has been shut down to prevent damage",
            "to your computer.",
            "",
            "FPS.EXE - CRITICAL_WEAPON_FAULT",
            "",
            "If this is the first time you've seen this Stop error screen,",
            "restart your computer. If this screen appears again, follow these steps:",
            "",
            "Check to make sure any new hardware or software is properly installed.",
            "Disable weapon overclocking options in simulation config.",
            "",
            "Technical information:",
            "*** STOP: 0x0000FPSC (0xRPG00001, 0x00000002, 0x00000000, 0x00000000)",
        ]

        y = 84
        for line in lines:
            self.canvas.create_text(58, y, anchor="nw", text=line, fill="#ffffff", font=("Consolas", 18))
            y += 34

        if elapsed > 3.0:
            self.canvas.create_text(
                58,
                HEIGHT - 70,
                anchor="nw",
                text="Press R to restart simulation or Esc to quit.",
                fill="#ffffff",
                font=("Consolas", 18, "bold"),
            )
