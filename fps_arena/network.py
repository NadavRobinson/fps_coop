"""Co-op networking host/client transport."""

import json
import queue
import socket
import threading
import time

from .config import CONNECT_TIMEOUT_SECONDS
from .models import _NetConn


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
