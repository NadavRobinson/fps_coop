import math
import random
import time
import tkinter as tk
import ctypes
import argparse
import json
import queue
import socket
import threading
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field

HAS_WIN32 = hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32")

WIDTH = 1280
HEIGHT = 720
HALF_HEIGHT = HEIGHT // 2
FOV = math.pi / 3
RAY_COUNT = 220
MAX_DEPTH = 20.0
PLAYER_RADIUS = 0.22
TARGET_FPS = 60
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


@dataclass
class Bot:
    x: float
    y: float
    health: float
    speed: float
    fire_cooldown: float = 0.0
    ai_cooldown: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    state: str = "advance"
    alive: bool = True
    radius: float = 0.28


@dataclass
class MoneyDrop:
    x: float
    y: float
    value: int
    ttl: float = 24.0


def make_owned_weapons() -> dict[str, bool]:
    return {name: (name == "pistol") for name in WEAPON_ORDER}


def make_ammo() -> dict[str, int]:
    return {
        "pistol": WEAPON_DATA["pistol"]["ammo_pack"],
        "shotgun": 0,
        "rifle": 0,
        "rpg": 0,
    }


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
    owned_weapons: dict[str, bool] = field(default_factory=make_owned_weapons)
    ammo: dict[str, int] = field(default_factory=make_ammo)
    next_fire_at: float = 0.0
    time_since_damage: float = 0.0
    keys: set[str] = field(default_factory=set)
    shooting: bool = False


@dataclass
class TeammateView:
    player_id: str
    name: str
    x: float
    y: float
    angle: float
    health: float
    weapon: str


@dataclass
class _NetConn:
    sock: socket.socket
    buffer: str = ""


class CoopHostServer:
    def __init__(self, host: str, port: int) -> None:
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((host, port))
        self.server.listen(8)
        self.server.setblocking(False)

        self.clients: dict[str, _NetConn] = {}
        self.incoming: queue.Queue[dict] = queue.Queue()
        self.outgoing: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.running = True
        self.next_player_id = 1

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _send_raw(self, player_id: str, payload: dict) -> None:
        conn = self.clients.get(player_id)
        if conn is None:
            return
        try:
            conn.sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except OSError:
            self._disconnect(player_id)

    def _disconnect(self, player_id: str) -> None:
        conn = self.clients.pop(player_id, None)
        if conn is None:
            return
        try:
            conn.sock.close()
        except OSError:
            pass
        self.incoming.put({"event": "disconnect", "player_id": player_id})

    def _accept_clients(self) -> None:
        while True:
            try:
                sock, _addr = self.server.accept()
            except BlockingIOError:
                break
            except OSError:
                return

            player_id = f"p{self.next_player_id}"
            self.next_player_id += 1
            sock.setblocking(False)
            self.clients[player_id] = _NetConn(sock=sock)
            self.incoming.put({"event": "connect", "player_id": player_id})
            self._send_raw(player_id, {"type": "welcome", "player_id": player_id})

    def _pump_outgoing(self) -> None:
        while True:
            try:
                target, payload = self.outgoing.get_nowait()
            except queue.Empty:
                break

            if target == "*":
                for pid in list(self.clients.keys()):
                    self._send_raw(pid, payload)
            else:
                self._send_raw(target, payload)

    def _pump_incoming(self) -> None:
        for player_id, conn in list(self.clients.items()):
            try:
                data = conn.sock.recv(65536)
            except BlockingIOError:
                continue
            except OSError:
                self._disconnect(player_id)
                continue

            if not data:
                self._disconnect(player_id)
                continue

            conn.buffer += data.decode("utf-8", errors="ignore")
            while "\n" in conn.buffer:
                line, conn.buffer = conn.buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.incoming.put({"event": "message", "player_id": player_id, "message": message})

    def _run(self) -> None:
        while self.running:
            self._accept_clients()
            self._pump_outgoing()
            self._pump_incoming()
            time.sleep(0.01)

        for conn in self.clients.values():
            try:
                conn.sock.close()
            except OSError:
                pass
        self.clients.clear()
        try:
            self.server.close()
        except OSError:
            pass

    def send(self, target: str, payload: dict) -> None:
        self.outgoing.put((target, payload))

    def poll(self) -> list[dict]:
        events: list[dict] = []
        while True:
            try:
                events.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return events

    def stop(self) -> None:
        self.running = False


class CoopClient:
    def __init__(self, host: str, port: int, name: str) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(CONNECT_TIMEOUT_SECONDS)
        try:
            self.sock.connect((host, port))
        finally:
            self.sock.settimeout(None)
        self.sock.setblocking(False)

        self.incoming: queue.Queue[dict] = queue.Queue()
        self.outgoing: queue.Queue[dict] = queue.Queue()
        self.buffer = ""
        self.running = True

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

        self.send({"type": "hello", "name": name})

    def _run(self) -> None:
        while self.running:
            while True:
                try:
                    payload = self.outgoing.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                except OSError:
                    self.incoming.put({"event": "disconnect"})
                    self.running = False
                    break

            if not self.running:
                break

            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                time.sleep(0.01)
                continue
            except OSError:
                self.incoming.put({"event": "disconnect"})
                break

            if not data:
                self.incoming.put({"event": "disconnect"})
                break

            self.buffer += data.decode("utf-8", errors="ignore")
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.incoming.put({"event": "message", "message": message})

        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, payload: dict) -> None:
        self.outgoing.put(payload)

    def poll(self) -> list[dict]:
        events: list[dict] = []
        while True:
            try:
                events.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return events

    def stop(self) -> None:
        self.running = False


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

        if mode == "host":
            self.coop_server = CoopHostServer("0.0.0.0", port)
            self.net_status = f"Hosting co-op on port {port}"
        elif mode == "client":
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

        title = "FPS Bot Arena"
        if mode == "host":
            title += " [CO-OP HOST]"
        elif mode == "client":
            title += " [CO-OP CLIENT]"

        self.root.title(title)
        self.root.geometry(f"{WIDTH}x{HEIGHT}")
        self.root.configure(bg="#111")

        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, bg="#101012", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.keys: set[str] = set()
        self.mouse_down = False
        self.last_mouse_x = WIDTH // 2
        self.last_mouse_y = HALF_HEIGHT
        self.mouse_sensitivity = 0.003

        self.last_time = time.perf_counter()
        self.damage_flash = 0.0
        self.muzzle_flash_timer = 0.0
        self.weapon_kick = 0.0

        self.mouse_locked = True
        self.mouse_ignore_event = False
        self.focused = True

        self._build_floor_cells()
        self._build_cover_points()
        self.reset_game()

        self.root.bind("<KeyPress>", self.on_key_down)
        self.root.bind("<KeyRelease>", self.on_key_up)
        self.root.bind("<Motion>", self.on_mouse_move)
        self.root.bind("<ButtonPress-1>", self.on_mouse_down)
        self.root.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.root.bind("<FocusIn>", self.on_focus_in)
        self.root.bind("<FocusOut>", self.on_focus_out)
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.set_mouse_capture(True)

        self.loop()

    def reset_game(self) -> None:
        self.player_x = 2.6
        self.player_y = 2.6
        self.player_angle = 0.15
        self.player_health = 100.0
        self.player_money = 0

        self.owned_weapons = make_owned_weapons()
        self.ammo = make_ammo()
        self.current_weapon = "pistol"
        self.next_fire_at = 0.0

        self.shop_open = False
        self.wave = 0
        self.wave_timer = 0.0

        self.bots: list[Bot] = []
        self.money_drops: list[MoneyDrop] = []

        self.game_state = "playing"
        self.glitch_timer = 0.0
        self.bsod_started_at = 0.0
        self.muzzle_flash_timer = 0.0
        self.weapon_kick = 0.0
        self.time_since_damage = 0.0

        self.remote_render_players = []
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
                remote.next_fire_at = 0.0
                remote.time_since_damage = 0.0
                remote.keys.clear()
                remote.shooting = False

        if self.net_mode != "client":
            self.spawn_wave()

        self.set_mouse_capture(True)

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
                self.remote_players[player_id] = RemotePlayer(
                    player_id=player_id,
                    name=f"Teammate {player_id}",
                    x=spawn_x,
                    y=spawn_y,
                    angle=random.uniform(0.0, math.tau),
                )
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
                    allowed = {"w", "a", "s", "d", "shift_l", "shift_r", "left", "right"}
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
        you = payload.get("you", {})
        self.player_x = float(you.get("x", self.player_x))
        self.player_y = float(you.get("y", self.player_y))
        self.player_angle = normalize_angle(float(you.get("angle", self.player_angle)))
        self.player_health = float(you.get("health", self.player_health))
        self.player_money = int(you.get("money", self.player_money))
        self.current_weapon = str(you.get("weapon", self.current_weapon))

        ammo_data = you.get("ammo")
        if isinstance(ammo_data, dict):
            for weapon in WEAPON_ORDER:
                if weapon in ammo_data:
                    self.ammo[weapon] = int(ammo_data[weapon])

        owned_data = you.get("owned")
        if isinstance(owned_data, dict):
            for weapon in WEAPON_ORDER:
                if weapon in owned_data:
                    self.owned_weapons[weapon] = bool(owned_data[weapon])

        self.wave = int(payload.get("wave", self.wave))
        self.game_state = str(payload.get("game_state", self.game_state))

        self.bots = []
        for item in payload.get("bots", []):
            self.bots.append(
                Bot(
                    x=float(item.get("x", 0.0)),
                    y=float(item.get("y", 0.0)),
                    health=float(item.get("health", 100.0)),
                    speed=1.2,
                    state=str(item.get("state", "advance")),
                    alive=bool(item.get("alive", True)),
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

        self.remote_render_players = []
        for item in payload.get("players", []):
            player_id = str(item.get("id", ""))
            if player_id == self.player_id:
                continue
            self.remote_render_players.append(
                TeammateView(
                    player_id=player_id,
                    name=str(item.get("name", "Teammate")),
                    x=float(item.get("x", 0.0)),
                    y=float(item.get("y", 0.0)),
                    angle=float(item.get("angle", 0.0)),
                    health=float(item.get("health", 0.0)),
                    weapon=str(item.get("weapon", "pistol")),
                )
            )

        self.net_status = f"Connected teammates: {len(self.remote_render_players)}"

    def send_client_input(self, now: float) -> None:
        if self.coop_client is None or not self.client_connected:
            return
        if now - self.last_net_send < self.net_send_interval:
            return

        self.last_net_send = now
        allowed = ["w", "a", "s", "d", "shift_l", "shift_r", "left", "right"]
        pressed = [key for key in allowed if key in self.keys]
        self.coop_client.send(
            {
                "type": "input",
                "keys": pressed,
                "angle": self.player_angle,
                "shoot": bool(self.mouse_down and not self.shop_open and self.game_state == "playing"),
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
            "money": remote.money,
            "weapon": remote.current_weapon,
            "ammo": dict(remote.ammo),
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
            "money": self.player_money,
            "weapon": self.current_weapon,
            "ammo": dict(self.ammo),
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
            {"x": b.x, "y": b.y, "health": b.health, "state": b.state, "alive": b.alive}
            for b in self.bots
            if b.alive
        ]
        drops = [{"x": d.x, "y": d.y, "value": d.value, "ttl": d.ttl} for d in self.money_drops]

        for remote in self.remote_players.values():
            payload = {
                "type": "snapshot",
                "you": self.serialize_remote(remote),
                "players": players,
                "bots": bots,
                "drops": drops,
                "wave": self.wave,
                "game_state": self.game_state,
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
        remote.current_weapon = weapon

    def update_remote_players(self, dt: float, now: float) -> None:
        for remote in self.remote_players.values():
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

            self.handle_remote_shooting(remote, now)

    def handle_remote_shooting(self, remote: RemotePlayer, now: float) -> None:
        if not remote.shooting or remote.health <= 0:
            return
        if now < remote.next_fire_at:
            return

        weapon = remote.current_weapon
        config = WEAPON_DATA[weapon]
        if not config["infinite"] and remote.ammo[weapon] <= 0:
            remote.current_weapon = "pistol"
            return

        remote.next_fire_at = now + config["fire_rate"]
        if not config["infinite"]:
            remote.ammo[weapon] = max(0, remote.ammo[weapon] - 1)

        if weapon == "rpg":
            self.game_state = "glitch"
            self.glitch_timer = 1.2
            return

        for _ in range(config["pellets"]):
            shot_angle = remote.angle + random.uniform(-config["spread"], config["spread"])
            target = self.get_first_bot_hit_from(remote.x, remote.y, shot_angle, config["range"])
            if target is None:
                continue
            target.health -= config["damage"]
            if target.health <= 0 and target.alive:
                self.kill_bot(target)

        if not config["infinite"] and remote.ammo[weapon] <= 0:
            remote.current_weapon = "pistol"

    def all_humans_dead(self) -> bool:
        if self.player_health > 0:
            return False
        for remote in self.remote_players.values():
            if remote.health > 0:
                return False
        return True

    def on_key_down(self, event: tk.Event) -> None:
        key = event.keysym.lower()
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

        if key == "escape":
            if self.shop_open:
                self.shop_open = False
                self.set_mouse_capture(True)
            else:
                self.on_close()

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
        self.release_cursor_clip()
        self.canvas.configure(cursor="arrow")

    def on_window_configure(self, _event: tk.Event) -> None:
        if self.mouse_locked and self.focused:
            self.clip_cursor_to_canvas()

    def on_close(self) -> None:
        self.release_cursor_clip()
        if self.coop_server is not None:
            self.coop_server.stop()
        if self.coop_client is not None:
            self.coop_client.stop()
        self.root.destroy()

    def set_mouse_capture(self, capture: bool) -> None:
        self.mouse_locked = capture
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
        self.root.update_idletasks()
        cx = self.canvas.winfo_rootx() + self.canvas.winfo_width() // 2
        cy = self.canvas.winfo_rooty() + self.canvas.winfo_height() // 2
        self.mouse_ignore_event = False
        if HAS_WIN32:
            self.mouse_ignore_event = True
            ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
        self.last_mouse_x = self.canvas.winfo_width() // 2
        self.last_mouse_y = self.canvas.winfo_height() // 2

    def clip_cursor_to_canvas(self) -> None:
        if not HAS_WIN32:
            return
        self.root.update_idletasks()
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
        if self.mouse_ignore_event:
            self.mouse_ignore_event = False
            self.last_mouse_x = event.x
            self.last_mouse_y = event.y
            return

        if not self.mouse_locked:
            self.last_mouse_x = event.x
            self.last_mouse_y = event.y
            return

        dx = event.x - self.last_mouse_x
        self.player_angle += dx * self.mouse_sensitivity
        self.player_angle = normalize_angle(self.player_angle)
        self.center_mouse()

    def on_mouse_down(self, _event: tk.Event) -> None:
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

    def buy_or_equip(self, weapon: str) -> None:
        if self.game_state != "playing":
            return

        config = WEAPON_DATA[weapon]
        if self.owned_weapons[weapon]:
            self.current_weapon = weapon
            return

        if self.player_money < config["cost"]:
            return

        self.player_money -= config["cost"]
        self.owned_weapons[weapon] = True
        self.ammo[weapon] += config["ammo_pack"]
        self.current_weapon = weapon

    def loop(self) -> None:
        now = time.perf_counter()
        dt = min(now - self.last_time, 0.05)
        self.last_time = now

        self.update(dt, now)
        self.render(now)

        ms = int(1000 / TARGET_FPS)
        self.root.after(ms, self.loop)
    def update(self, dt: float, now: float) -> None:
        if self.net_mode == "client":
            self.process_client_network_events()
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
            self.time_since_damage += dt
            if self.player_health > 0:
                self.update_player_movement(dt)
                self.handle_shooting(now)
            if self.net_mode == "host":
                self.update_remote_players(dt, now)

            self.update_bots(dt)
            self.update_drops(dt)

            if self.player_health <= 0:
                self.player_health = 0
            elif self.player_health < 100.0 and self.time_since_damage >= HEALTH_REGEN_DELAY:
                self.player_health = min(100.0, self.player_health + HEALTH_REGEN_RATE * dt)

            if self.all_humans_dead():
                self.game_state = "dead"
                self.set_mouse_capture(False)

            if self.alive_bots() == 0:
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

    def update_player_movement(self, dt: float) -> None:
        speed = 3.2
        if "shift_l" in self.keys or "shift_r" in self.keys:
            speed = 4.2

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
        if self.player_health > 0:
            candidates.append(("host", self.player_x, self.player_y))
        if self.net_mode == "host":
            for remote in self.remote_players.values():
                if remote.health > 0:
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

            if has_los and dist_to_player < 11.5 and bot.fire_cooldown <= 0:
                base_hit = 0.78 - dist_to_player * 0.055
                if bot.state == "cover":
                    base_hit += 0.08
                hit_chance = clamp(base_hit, 0.2, 0.84)

                if random.random() < hit_chance:
                    dmg = random.randint(4, 9) + self.wave // 3
                    if target_id == "host":
                        self.player_health -= dmg
                        self.time_since_damage = 0.0
                        self.damage_flash = 0.45
                    else:
                        remote = self.remote_players.get(target_id)
                        if remote is not None:
                            remote.health -= dmg
                            remote.time_since_damage = 0.0

                bot.fire_cooldown = random.uniform(0.45, 1.05)

    def assign_bot_tactic(self, bot: Bot, target_x: float, target_y: float, has_los: bool, dist_to_player: float) -> None:
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
            if self.player_health > 0 and d_local < 0.56:
                collector = "host"
                collector_dist = d_local

            if self.net_mode == "host":
                for remote in self.remote_players.values():
                    if remote.health <= 0:
                        continue
                    d_remote = distance(drop.x, drop.y, remote.x, remote.y)
                    if d_remote < 0.56 and d_remote < collector_dist:
                        collector = remote.player_id
                        collector_dist = d_remote

            if collector == "host":
                self.player_money += drop.value
            elif isinstance(collector, str):
                remote = self.remote_players.get(collector)
                if remote is not None:
                    remote.money += drop.value
            else:
                kept.append(drop)

        self.money_drops = kept

    def handle_shooting(self, now: float) -> None:
        if self.net_mode == "client":
            return
        if self.player_health <= 0:
            return
        if self.shop_open:
            return
        if not self.mouse_down:
            return
        if now < self.next_fire_at:
            return

        weapon = self.current_weapon
        config = WEAPON_DATA[weapon]

        if not config["infinite"] and self.ammo[weapon] <= 0:
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
            self.ammo[weapon] = max(0, self.ammo[weapon] - 1)

        if weapon == "rpg":
            self.game_state = "glitch"
            self.glitch_timer = 1.2
            return

        pellets = config["pellets"]
        for _ in range(pellets):
            shot_angle = self.player_angle + random.uniform(-config["spread"], config["spread"])
            target = self.get_first_bot_hit(shot_angle, config["range"])
            if target is None:
                continue

            target.health -= config["damage"]
            if target.health <= 0 and target.alive:
                self.kill_bot(target)

        if not config["infinite"] and self.ammo[weapon] <= 0 and weapon != "pistol":
            self.current_weapon = "pistol"

    def get_first_bot_hit_from(self, origin_x: float, origin_y: float, shot_angle: float, max_range: float) -> Bot | None:
        cos_a = math.cos(shot_angle)
        sin_a = math.sin(shot_angle)

        closest = None
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
                closest_dist = along

        return closest

    def get_first_bot_hit(self, shot_angle: float, max_range: float) -> Bot | None:
        return self.get_first_bot_hit_from(self.player_x, self.player_y, shot_angle, max_range)

    def kill_bot(self, bot: Bot) -> None:
        bot.alive = False
        money_count = 1 if random.random() < 0.75 else 2
        for _ in range(money_count):
            value = random.randint(28, 62) + self.wave * 4
            ox = random.uniform(-0.16, 0.16)
            oy = random.uniform(-0.16, 0.16)
            self.money_drops.append(MoneyDrop(bot.x + ox, bot.y + oy, value))

    def spawn_wave(self) -> None:
        self.wave += 1
        spawn_count = min(4 + self.wave * 2, 24)

        if self.player_health <= 0:
            self.player_health = 65.0
            self.player_x, self.player_y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 4.0)
        else:
            self.player_health = min(100.0, self.player_health + 12.0)

        if self.net_mode == "host":
            for remote in self.remote_players.values():
                if remote.health <= 0:
                    remote.health = 65.0
                    remote.x, remote.y = self.pick_spawn_far_from_point(self.player_x, self.player_y, 4.0)
                else:
                    remote.health = min(100.0, remote.health + 12.0)

        reachable_cells = self.get_reachable_floor_cells()

        for _ in range(spawn_count):
            x, y = self.pick_spawn_far_from_player(reachable_cells)
            bot_hp = 65 + self.wave * 7
            bot_speed = 1.2 + min(0.6, self.wave * 0.04)
            self.bots.append(Bot(x=x, y=y, health=bot_hp, speed=bot_speed, target_x=x, target_y=y))

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
                    )
                )
        else:
            teammates = list(self.remote_render_players)

        for teammate in teammates:
            if teammate.health <= 0:
                continue
            d = distance(teammate.x, teammate.y, self.player_x, self.player_y)
            items.append((d, "human", teammate))

        items.sort(key=lambda item: item[0], reverse=True)

        for dist, kind, obj in items:
            dx = obj.x - self.player_x
            dy = obj.y - self.player_y
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
                h = int((HEIGHT * 0.72) / max(0.15, dist))
                w = int(h * 0.48)
                x1 = screen_x - w / 2
                y1 = HALF_HEIGHT - h / 2
                x2 = screen_x + w / 2
                y2 = HALF_HEIGHT + h / 2

                body = "#d64a4a" if bot.state != "cover" else "#c28a3e"
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

                self.canvas.create_rectangle(x1, y1, x2, y2, fill="#4a8ad6", outline="")
                head_h = h * 0.28
                self.canvas.create_oval(x1 + w * 0.2, y1 - head_h * 0.6, x2 - w * 0.2, y1 + head_h * 0.7, fill="#f1c7ac", outline="")
                self.canvas.create_text(screen_x, y1 - 14, text=teammate.name, fill="#bcd8ff", font=("Consolas", 10, "bold"))
            else:
                h = int((HEIGHT * 0.22) / max(0.2, dist))
                w = h
                x1 = screen_x - w / 2
                y1 = HALF_HEIGHT + h * 0.2
                x2 = screen_x + w / 2
                y2 = y1 + h
                self.canvas.create_oval(x1, y1, x2, y2, fill="#68d96f", outline="")

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
        hp_width = int(272 * (self.player_health / 100.0))
        hp_color = "#52cc52" if self.player_health > 35 else "#cc4a3f"
        self.canvas.create_rectangle(26, 26, 26 + hp_width, 54, fill=hp_color, outline="")
        self.canvas.create_text(162, 40, text=f"Health: {int(self.player_health)}", fill="#fff", font=("Consolas", 14, "bold"))

        ammo_text = "INF" if WEAPON_DATA[self.current_weapon]["infinite"] else str(self.ammo[self.current_weapon])
        weapon_name = WEAPON_DATA[self.current_weapon]["name"]
        self.canvas.create_text(26, 80, anchor="nw", text=f"Weapon: {weapon_name}", fill="#f3f3f3", font=("Consolas", 18, "bold"))
        self.canvas.create_text(26, 108, anchor="nw", text=f"Ammo: {ammo_text}", fill="#f3f3f3", font=("Consolas", 16))

        self.canvas.create_text(26, 138, anchor="nw", text=f"Money: ${self.player_money}", fill="#78e088", font=("Consolas", 18, "bold"))
        self.canvas.create_text(26, 166, anchor="nw", text=f"Wave: {self.wave}", fill="#dfdfdf", font=("Consolas", 16))
        self.canvas.create_text(26, 192, anchor="nw", text=f"Bots Alive: {self.alive_bots()}", fill="#dfdfdf", font=("Consolas", 16))

        teammate_count = len(self.remote_players) if self.net_mode == "host" else len(self.remote_render_players)
        if self.net_mode != "single":
            self.canvas.create_text(26, 218, anchor="nw", text=f"Teammates: {teammate_count}", fill="#9cc9ff", font=("Consolas", 16))
            self.canvas.create_text(26, 244, anchor="nw", text=self.net_status, fill="#9cc9ff", font=("Consolas", 12))

        help_text = "WASD + Mouse (locked) | B Shop/Unlock Cursor | 1-4 Quick Buy/Switch | R Restart"
        if self.net_mode == "client":
            help_text = "CO-OP Client | WASD+Mouse -> host | B Shop | 1-4 Buy/Switch"
        elif self.net_mode == "host":
            help_text = "CO-OP Host | Others can join via your IP + port"

        self.canvas.create_text(
            WIDTH - 22,
            24,
            anchor="ne",
            text=help_text,
            fill="#e0e0e0",
            font=("Consolas", 12),
        )

        self.draw_weapon_bar()

        if self.shop_open and self.game_state == "playing":
            self.render_shop_wheel()

        if self.damage_flash > 0:
            alpha = int(clamp(self.damage_flash * 120, 0, 120))
            color = rgb(110 + alpha, 18, 18)
            self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill=color, outline="", stipple="gray50")

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
                    extra = f" ({self.ammo[weapon]})"
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
                    txt += f"\nAmmo: {self.ammo[weapon]}"
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


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def normalize_angle(angle: float) -> float:
    while angle < 0:
        angle += math.tau
    while angle >= math.tau:
        angle -= math.tau
    return angle


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rgb(r: int, g: int, b: int) -> str:
    r = int(clamp(r, 0, 255))
    g = int(clamp(g, 0, 255))
    b = int(clamp(b, 0, 255))
    return f"#{r:02x}{g:02x}{b:02x}"


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
