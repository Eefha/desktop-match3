# -*- coding: utf-8 -*-
"""
联机大厅 + 两种玩法
--------------------
A 同盘竞速：房主发一块相同的棋盘，各玩各的，限时比分数
B 共享棋盘：只有一块板，谁先换成功谁得分（房主权威，先到先得）

房主就是服务器，不用云、不用账号。传输走局域网 TCP 或蓝牙 RFCOMM，上层协议一模一样。
棋子用的是**房主自己的桌面图标**：开场把图标拼成一张 PNG 传过去，两边看到的图标完全相同。
"""
from __future__ import annotations

import base64
import io
import random
import threading
import time

import pygame

import netcore
from engine import adjacent, resolve_swap

MODE_A, MODE_B = "A", "B"
MAX_PLAYERS = 5
RACE_SECONDS = 180
HEARTBEAT = 3.0
PEER_TIMEOUT = 14.0


def _fmt_left(sec):
    sec = max(0, int(sec + 0.5))
    return f"{sec // 60:02d}:{sec % 60:02d}"


class Netplay:
    def __init__(self, game):
        self.game = game
        self.role = None            # None / "host" / "client"
        self.mode = MODE_A
        self.active = False         # 是否在一个房间里（含等待）
        self.running = False        # 是否已经开局
        self.code = ""
        self.my_id = 0
        self.players = {}           # id -> {"id","n","s","m","lat"}
        self.conn = None            # 客户端的那条连接
        self.conns = {}             # 房主侧：id -> Conn
        self.listener = None
        self.btl = None
        self.beacon = None
        self.transport = "lan"
        self.err = ""
        self.started_at = 0.0
        self.left = float(RACE_SECONDS)
        self.panel = ""             # "" / wait / joining / status
        self.nick = ""              # 昵称由设置窗口填，留空就显示“玩家N”
        self.bt_channel = netcore.BT_CHANNEL
        self._lock = threading.Lock()
        self._new = []              # 待登记的新连接（accept 线程放，主线程取）
        self._last_scores = 0.0
        self._last_me = 0.0
        self._last_ping = 0.0
        self._last_peer = time.time()
        self._sheet = None
        self._over_sent = False
        self._pending_close = None
        self._ip = None

    # ================================================================= 工具
    def is_me(self, pid):
        return pid == self.my_id

    def name_of(self, pid):
        p = self.players.get(pid)
        if not p:
            return "?"
        if pid == self.my_id:
            return "我"
        return p.get("n") or f"玩家{pid}"

    def sorted_players(self):
        return sorted(self.players.values(), key=lambda p: (-p.get("s", 0), p["id"]))

    def rank_of(self, pid):
        for i, p in enumerate(self.sorted_players()):
            if p["id"] == pid:
                return i + 1
        return 0

    def _icon_sheet(self):
        """把房主的图标拼成一张 PNG（base64），对方直接照着画，看到的图标一模一样。"""
        if self._sheet is not None:
            return self._sheet
        try:
            from PIL import Image

            icons = self.game.icons
            size = 96
            sheet = Image.new("RGBA", (size * len(icons), size), (0, 0, 0, 0))
            for i, (_, im) in enumerate(icons):
                sheet.paste(im.convert("RGBA").resize((size, size), Image.LANCZOS),
                            (i * size, 0))
            buf = io.BytesIO()
            sheet.save(buf, "PNG", optimize=True)
            self._sheet = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            self._sheet = ""
        return self._sheet

    # ================================================================= 开房
    def create_room(self, mode, transport="lan"):
        self.close(silent=True)
        self.mode = mode
        self.role = "host"
        self.transport = transport
        self.my_id = 0
        self.players = {0: {"id": 0, "n": self.nick, "s": 0, "m": 0, "lat": 0}}
        self.conns = {}
        self.err = ""
        self.running = False
        self.left = float(RACE_SECONDS)
        self._over_sent = False
        self._sheet = None

        if transport == "lan":
            for _ in range(14):
                code = random.randint(1000, 9999)
                try:
                    self.listener = netcore.TcpListener(netcore.PORT_BASE + code)
                except OSError:
                    self.listener = None
                    continue
                self.code = str(code)
                break
            if not self.listener:
                self.err = "端口都被占用了，换个时间再试"
                self.close(silent=True)
                return False
            port = netcore.PORT_BASE + int(self.code)
            self.beacon = netcore.Beacon(lambda: {
                "t": "room", "code": self.code, "mode": self.mode,
                "host": self.nick, "n": len(self.players),
                "cap": MAX_PLAYERS, "port": port, "bt": "",
            })
        else:
            try:
                self.btl = netcore.BthListener(self.bt_channel)
            except OSError as exc:
                self.err = f"蓝牙开不了：{exc}"
                self.close(silent=True)
                return False
            self.code = self.btl.addr.replace(":", "")[-6:] or "BT"
        self.active = True
        if transport == "lan":
            threading.Thread(target=self._accept_loop, daemon=True).start()
        else:
            threading.Thread(target=self._bt_accept_loop, daemon=True).start()
        self.panel = "wait"
        self.game.reset_net_state()
        return True

    def _accept_loop(self):
        while self.active and self.listener and not self.listener.closed:
            got = self.listener.accept()
            if not got:
                continue
            sock, ip = got
            try:
                c = netcore.Conn(sock, ip)
            except Exception:  # noqa: BLE001
                continue
            with self._lock:
                self._new.append(c)
            self._last_peer = time.time()

    def _bt_accept_loop(self):
        while self.active and self.btl and not self.btl.closed:
            raw = self.btl.accept()
            if not raw:
                time.sleep(0.2)
                continue
            try:
                c = netcore.Conn(raw, raw.peer)
            except Exception:  # noqa: BLE001
                continue
            with self._lock:
                self._new.append(c)
            self._last_peer = time.time()

    # ================================================================= 客户端进房
    def join_room(self, room):
        """room: 广播里那份 dict（含 ip / port / code / mode）"""
        self.close(silent=True)
        self.role = "client"
        self.transport = "lan"
        self.err = ""
        self.active = False
        self.panel = "joining"
        self.mode = room.get("mode") or MODE_A
        self.code = str(room.get("code", ""))
        ip, port = room.get("ip"), int(room.get("port") or 0)
        threading.Thread(target=self._connect_worker,
                         args=("lan", ip, port), daemon=True).start()
        return True

    def join_bt(self, addr):
        self.close(silent=True)
        self.role = "client"
        self.transport = "bt"
        self.err = ""
        self.active = False
        self.panel = "joining"
        threading.Thread(target=self._connect_worker,
                         args=("bt", addr, self.bt_channel), daemon=True).start()
        return True

    def _connect_worker(self, kind, host, port):
        try:
            if kind == "lan":
                sock = netcore.tcp_connect(host, port, timeout=3.0)
                conn = netcore.Conn(sock, host)
            else:
                raw = netcore.bt_connect(host, port)
                conn = netcore.Conn(raw, host)
        except Exception as exc:  # noqa: BLE001
            self.err = f"连不上：{exc}"
            self.panel = ""
            self.role = None
            self.game._toast(self.err, 3.2)
            return
        self.conn = conn
        self._last_peer = time.time()
        conn.send({"t": "hello", "ver": netcore.PROTO_VER, "name": self.nick})
        self.panel = "joining"

    # ================================================================= 消息
    def tick(self, dt):
        if not self.active and not self.conn:
            return
        if self.role == "host":
            self._tick_host(dt)
        else:
            self._tick_client(dt)

    # ---------------------------------------------------------- 房主
    def _tick_host(self, dt):
        if self.beacon:
            self.beacon.tick()

        with self._lock:
            news, self._new = self._new, []
        for c in news:
            self._register(c)

        now = time.time()
        for pid, c in list(self.conns.items()):
            if not c.alive or now - c.last_rx > PEER_TIMEOUT:
                self._drop(pid)         # 对面退了 / 断链超时，把座位腾出来
                continue
            for m in c.poll():
                self._host_handle(pid, c, m)

        now = time.time()
        if now - self._last_ping > HEARTBEAT:
            self._last_ping = now
            for c in self.conns.values():
                c.ping()

        if self.running and self.mode == MODE_A:
            self.left -= dt
            if self.left <= 0:
                self.left = 0.0
                self.finish("time")
                return

        self._push_scores(dt)

    def _register(self, c):
        free = [i for i in range(1, MAX_PLAYERS) if i not in self.players]
        if not free:
            c.send({"t": "full"})
            c.close()
            return
        pid = free[0]
        self.players[pid] = {"id": pid, "n": f"玩家的{pid}", "s": 0, "m": 0, "lat": 0}
        self.conns[pid] = c
        c.send(self._welcome(pid))
        self._broadcast({"t": "lobby", "p": self._players_payload()})
        self.game._toast(f"有人加入了（房间 {self.code}）", 2.0)

        # 第一个人进来就自动开局，省得房主还去点一下
        if not self.running and len(self.conns) == 1:
            self._start_countdown()

    def _start_countdown(self):
        if self.running:
            return
        for i, txt in enumerate(("3", "2", "1")):
            self.game.schedule(0.5 + i * 0.7, lambda t=txt: self.game._toast(t, 0.6))
        self.game.schedule(2.6, self.start_match)

    def _welcome(self, pid):
        g = self.game
        return {
            "t": "welcome", "ver": netcore.PROTO_VER, "pid": pid,
            "code": self.code, "mode": self.mode, "host": self.nick,
            "rows": g.rows, "cols": g.cols, "ntypes": g.ntypes,
            "names": [n for n, _ in g.icons], "sheet": self._icon_sheet(),
            "started": self.running,
            "grid": g.board.dump() if self.running else None,
            "left": round(self.left, 1),
            "p": self._players_payload(),
        }

    def _players_payload(self):
        return [[p["id"], p.get("n", ""), int(p.get("s", 0)), int(p.get("m", 0))]
                for p in self.players.values()]

    def _apply_players(self, rows):
        for row in rows or []:
            try:
                pid, n, s, m = row
            except Exception:  # noqa: BLE001
                continue
            p = self.players.get(pid)
            if p is None:
                self.players[pid] = {"id": pid, "n": n, "s": s, "m": m, "lat": 0}
            else:
                p["n"], p["s"], p["m"] = n, s, m

    def _host_handle(self, pid, conn, m):
        t = m.get("t")
        self._last_peer = time.time()
        if t == "ping":
            conn.pong(m.get("ts"))
            return
        if t == "pong":
            conn.note_pong(m.get("ts"))
            if pid in self.players:
                self.players[pid]["lat"] = int(conn.rtt)
            return
        if t == "hello":
            nm = (m.get("name") or "").strip()[:8]
            if pid in self.players and nm:
                self.players[pid]["n"] = nm
            self._broadcast({"t": "lobby", "p": self._players_payload()})
            return
        if t == "me":                       # A 模式：对方报分数
            p = self.players.get(pid)
            if p:
                p["s"] = int(m.get("s", 0))
                p["m"] = int(m.get("m", 0))
            return
        if t == "swap" and self.mode == MODE_B:
            a, b = m.get("a"), m.get("b")
            if isinstance(a, list) and isinstance(b, list) and len(a) == 2 and len(b) == 2:
                self.host_apply_swap(pid, (a[0], a[1]), (b[0], b[1]))
            return
        if t == "shuffle" and self.mode == MODE_B:
            self.host_shuffle(pid)
            return
        if t == "rematch":
            if self.game.state == "over":
                self.start_match()
            return
        if t == "bye":
            self._drop(pid, "对方离开了")

    def _drop(self, pid, why=""):
        c = self.conns.pop(pid, None)
        if c:
            c.close()
        p = self.players.pop(pid, None)
        if p:
            self.game._toast(f"{p.get('n') or '玩家'} 离开了", 2.0)
        self._broadcast({"t": "lobby", "p": self._players_payload()})

    def _broadcast(self, obj):
        for c in list(self.conns.values()):
            c.send(obj)

    def _push_scores(self, dt):
        now = time.time()
        me = self.players.get(0)
        if me:
            me["s"] = int(self.game.score)
            me["m"] = int(self.game.moves)
        if self.mode != MODE_A and not self.running:
            return
        if now - self._last_scores < 0.25:
            return
        self._last_scores = now
        self._broadcast({"t": "scores", "p": self._players_payload(),
                         "left": round(self.left, 1)})

    # ---------------------------------------------------------- 客户端
    def _tick_client(self, dt):
        c = self.conn
        if not c:
            return
        if not c.alive:
            if self.active:
                self.game._toast("和房主断开了", 3.0)
                self.game.net_locked = False
                self.close(silent=True)
            return
        for m in c.poll():
            self._client_handle(m)
        now = time.time()
        if now - self._last_ping > HEARTBEAT:
            self._last_ping = now
            c.ping()

        if self.active and self.running and self.mode == MODE_A:
            me = self.players.get(self.my_id)
            if me:
                me["s"] = int(self.game.score)
                me["m"] = int(self.game.moves)
            if now - self._last_me > 0.3:
                self._last_me = now
                c.send({"t": "me", "s": int(self.game.score), "m": int(self.game.moves)})

    def _client_handle(self, m):
        t = m.get("t")
        self._last_peer = time.time()
        if t == "ping":
            if self.conn:
                self.conn.pong(m.get("ts"))
            return
        if t == "pong":
            if self.conn:
                self.conn.note_pong(m.get("ts"))
            return
        if t == "_closed":
            if self.active:
                self.game._toast("和房主断开了", 3.0)
                self.game.net_locked = False
                self.close(silent=True)
            return
        if t == "welcome":
            self._on_welcome(m)
            return
        if t == "full":
            self.err = "房间满了（最多 5 人）"
            self.close(silent=True)
            self.panel = ""
            self.game._toast(self.err, 3.0)
            return
        if t == "lobby":
            self._apply_players(m.get("p"))
            return
        if t == "scores":
            self._apply_players(m.get("p"))
            self.left = float(m.get("left", self.left))
            me = self.players.get(self.my_id)
            if me:
                if self.mode == MODE_A:
                    # 竞速模式自己的分自己算，只把别人的分收下来；
                    # 否则房主那边还没收到上报时，会把自己刚得的分冲回 0
                    me["s"] = int(self.game.score)
                    me["m"] = int(self.game.moves)
                else:
                    self.game.score = int(me.get("s", 0))
                    self.game.moves = int(me.get("m", 0))
            return
        if t == "start":
            self._on_start(m)
            return
        if t == "state":
            self._on_state(m)
            return
        if t == "deny":
            self.game.net_locked = False
            self.game.busy = False
            self.game._toast(m.get("why") or "这步没成，可能被抢先了", 1.6)
            return
        if t == "over":
            self._on_over(m)
            return

    def _on_welcome(self, m):
        if int(m.get("ver", 0)) != netcore.PROTO_VER:
            self.err = "版本不一致，换同一个 exe 再试"
            self.close(silent=True)
            self.panel = ""
            self.game._toast(self.err, 3.4)
            return
        self.my_id = int(m.get("pid", 1))
        self.code = str(m.get("code", ""))
        self.mode = m.get("mode") or MODE_A
        self.active = True
        self.panel = ""
        self._apply_players(m.get("p"))
        self.game.adopt_net(int(m.get("rows", 0)), int(m.get("cols", 0)),
                            int(m.get("ntypes", 0)), m.get("names") or [],
                            m.get("sheet") or "")
        self.left = float(m.get("left", RACE_SECONDS))
        if m.get("started") and m.get("grid"):
            if self.mode == MODE_A:
                self._on_start({"grid": m["grid"], "left": self.left,
                                "rows": m.get("rows"), "cols": m.get("cols")})
            else:
                self.running = True
                self.game.net_sync(m["grid"], [])
                self.game.state = "play"
                self.game.busy = False
        self.game._toast(f"已进入房间 {self.code} · 模式{self.mode}", 2.2)

    def _on_start(self, m):
        grid = m.get("grid")
        if not grid:
            return
        self.running = True
        self.left = float(m.get("left", RACE_SECONDS))
        if self.mode == MODE_B:
            # 共享棋盘：不开自己的局，直接照着房主这块板画
            self.game.net_started = True
            self.game.state = "play"
            self.game.busy = False
            self.game.score = 0
            self.game.moves = 0
            self.game.manual = self.game.MANUAL
            self.game.reshuffles = self.game.RESHUFFLES
            self.game.net_sync(grid, [])
            self.game._toast("共享棋盘 · 谁先换成功谁得分", 2.0)
            return
        self.game.start_net_race(grid, self.left)
        self.game._toast("同盘竞速开始 · 比分数", 1.6)

    def _on_state(self, m):
        grid = m.get("grid")
        if not grid:
            return
        self._apply_players(m.get("p"))
        me = self.players.get(self.my_id)
        if me:
            self.game.score = int(me.get("s", 0))
            self.game.moves = int(me.get("m", 0))
        self.game.net_locked = False
        self.game.busy = False
        self.game.net_sync(grid, m.get("cleared") or [])

    def _on_over(self, m):
        self._apply_players(m.get("p"))
        self.running = False
        self.game.state = "over"
        self.game.busy = True
        self.game.net_locked = False
        self.game.net_over = {"reason": m.get("reason") or "time",
                              "rows": sorted(
                                  [{"id": r[0], "n": r[1], "s": r[2]} for r in
                                   (m.get("stand") or [])],
                                  key=lambda x: -x["s"])}

    # ================================================================= 开局
    def start_match(self):
        if self.role != "host" or not self.active:
            return
        self._over_sent = False
        self.game.net_over = None
        self.game.reset_net_state()
        if self.mode == MODE_A:
            self.game.new_game()          # 房主新开一块，然后把它发给所有人
            self.game.busy = False
            self.running = True
            self.left = float(RACE_SECONDS)
            self.game.net_started = True
            self._broadcast({"t": "start", "grid": self.game.board.dump(),
                             "rows": self.game.rows, "cols": self.game.cols,
                             "dur": RACE_SECONDS, "left": self.left})
            self.game._toast("同盘竞速开始 · 比分数", 1.6)
        else:
            self.game.new_game()
            self.game.busy = False
            self.running = True
            self.game.net_started = True
            self.game.reshuffles = self.game.RESHUFFLES
            self._broadcast({"t": "start", "grid": self.game.board.dump(),
                             "rows": self.game.rows, "cols": self.game.cols})
            self._broadcast_state([])
            self.game._toast("共享棋盘 · 谁先换成功谁得分", 2.0)
        self._broadcast({"t": "scores", "p": self._players_payload(),
                         "left": round(self.left, 1)})

    def finish(self, reason="time"):
        if self.role != "host" or self._over_sent:
            return
        self._over_sent = True
        self.running = False
        me = self.players.get(0)
        if me:
            me["s"] = int(self.game.score)
            me["m"] = int(self.game.moves)
        stand = self._players_payload()
        self._broadcast({"t": "over", "reason": reason, "stand": stand,
                         "p": stand})
        self.game.state = "over"
        self.game.busy = True
        self.game.net_over = {
            "reason": reason,
            "rows": sorted([{"id": r[0], "n": r[1], "s": r[2]} for r in stand],
                           key=lambda x: -x["s"]),
        }

    # ================================================================= B 模式
    def on_local_swap(self, a, b) -> bool:
        """玩家自己动手了。返回 True 表示这次交换由联机接管，本地不要自己结算。"""
        if not self.active or self.mode != MODE_B or not self.running:
            return False
        if self.role == "host":
            self.host_apply_swap(0, a, b)
            return True
        if self.conn and self.conn.alive:
            self.conn.send({"t": "swap", "a": list(a), "b": list(b)})
            self.game.net_locked = True
            self.game.busy = True
            self.game.schedule(1.4, self._unlock_timeout)
        else:
            self.game._toast("连接断了", 1.6)
        return True

    def _unlock_timeout(self):
        if self.game.net_locked:
            self.game.net_locked = False
            self.game.busy = False
            self.game._toast("网络有点慢，这步没生效", 1.6)

    def host_apply_swap(self, pid, a, b):
        g = self.game
        if not adjacent(a, b) or not self.running:
            return
        res = resolve_swap(g.board, a, b)
        if not res:
            if pid == 0:
                g._toast("这样换不出三连", 1.2)
            elif pid in self.conns:
                self.conns[pid].send({"t": "deny", "why": "这样换不出三连"})
            return
        p = self.players.get(pid) or {"id": pid, "n": f"玩家{pid}", "s": 0, "m": 0}
        p["s"] = int(p.get("s", 0)) + int(res["gain"])
        p["m"] = int(p.get("m", 0)) + 1
        self.players[pid] = p
        g.score = int(self.players[0]["s"])
        g.moves = int(self.players[0]["m"])
        g.net_apply(res["cells"], res["gain"], by=pid)
        self._broadcast_state(res["cells"], pid, res["gain"])
        self._check_dead()

    def _broadcast_state(self, cleared, by=None, gain=None):
        if self.role != "host":
            return
        self._broadcast({"t": "state", "grid": self.game.board.dump(),
                         "cleared": [list(c) for c in (cleared or [])],
                         "by": by, "gain": gain,
                         "p": self._players_payload()})

    def _check_dead(self):
        g = self.game
        if g.board.has_move():
            return
        if g.reshuffles > 0:
            g.reshuffles -= 1
            g.board.shuffle()
            g.net_rebuild_now()
            g._toast(f"没有可消除的组合 · 已重新排列（剩 {g.reshuffles} 次）", 2.6)
            self._broadcast_state([])
            return
        self.finish("nomoves")

    def host_shuffle(self, pid):
        g = self.game
        if g.manual <= 0:
            if pid == 0:
                g._toast("重新排列已用完（每局 3 次）", 2.0)
            elif pid in self.conns:
                self.conns[pid].send({"t": "deny", "why": "重新排列已用完"})
            return
        g.manual -= 1
        if pid == 0:
            g._toast(f"已重新排列 · 剩 {g.manual} 次", 1.8)
        g.board.shuffle()
        g.net_rebuild_now()
        self._broadcast_state([])

    def request_shuffle(self) -> bool:
        """B 模式里点「重新排列」：房里只有房主说了算，其他人发请求。"""
        if self.role == "host":
            self.host_shuffle(0)
            return True
        if self.conn and self.conn.alive:
            self.conn.send({"t": "shuffle"})
            return True
        return False

    # ================================================================= 退出
    def leave_game(self):
        """结算卡片上的「退出房间」/ 关房间。"""
        if self.role == "host":
            self._broadcast({"t": "over", "reason": "host_left", "stand": [],
                             "p": self._players_payload()})
        else:
            if self.conn:
                self.conn.send({"t": "bye"})
        self.close(silent=True)

    def close(self, silent=False):
        self.active = False
        self.running = False
        if self.beacon:
            self.beacon.close()
            self.beacon = None
        if self.listener:
            self.listener.close()
            self.listener = None
        if self.btl:
            self.btl.close()
            self.btl = None
        for c in list(self.conns.values()):
            c.close()
        self.conns = {}
        if self.conn:
            self.conn.close()
            self.conn = None
        if not silent:
            self.players = {}
            self.role = None
            self.panel = ""
            self.game.reset_net_state()
        else:
            self.players = {}
            self.role = None
            self.game.reset_net_state()

    # ================================================================= 面板
    def toggle_panel(self):
        """Ctrl+J：已经在房间里就看实时比分，没联机就交给设置子窗口。"""
        if self.panel:
            self.panel = ""
            return
        if not self.active and not self.running:
            self.game.open_settings()
            return
        self.panel = "status"

    def handle_event(self, e) -> bool:
        if not self.panel:
            return False
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                if self.panel == "joining":
                    self.close(silent=True)    # 放弃这次连接
                self.panel = ""
                return True
            if self.panel == "wait" and e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.conns and not self.running:
                    self._start_countdown()
                return True
            if self.panel == "status" and e.key == pygame.K_x:
                self.leave_game()
                self.panel = ""
                return True
        return True

    # ---------------------------------------------------------- 画面板
    def draw_panel(self):
        if not self.panel:
            return
        g = self.game
        scr = g.screen
        lines = []
        title = ""
        foot = ""

        if self.panel == "wait":
            title = f"房间 {self.code}"
            mode = "同盘竞速" if self.mode == MODE_A else "共享棋盘"
            if self.transport == "lan":
                if not self._ip:
                    self._ip = netcore.local_ip()
                port = netcore.PORT_BASE + int(self.code or 0)
                where = f"局域网　{self._ip}:{port}"
            else:
                addr = self.btl.addr if self.btl else ""
                where = f"蓝牙　{addr}　通道 {self.bt_channel}"
            lines.append((f"{mode}　{len(self.players)}/{MAX_PLAYERS} 人　{where}",
                          (170, 178, 192)))
            for p in self.sorted_players():
                lines.append((f"· {self.name_of(p['id'])}", (226, 232, 244)))
            if self.running:
                foot = "已经开局　Esc 收起面板"
            elif self.conns:
                foot = "对方已进来，即将自动开局　Esc 散伙"
            else:
                foot = "等对方进来（自动开局）　Esc 散伙"

        elif self.panel == "joining":
            title = "正在连接…"
            lines.append((f"房间 {self.code or self.mode}", (226, 232, 244)))
            if self.err:
                lines.append((self.err, (240, 150, 150)))
            foot = "Esc 取消"

        else:   # status
            title = "联机中"
            mode = "同盘竞速" if self.mode == MODE_A else "共享棋盘"
            role = "房主" if self.role == "host" else "成员"
            left = (f"　剩 {_fmt_left(self.left)}" if self.mode == MODE_A and self.running
                    else "")
            lines.append((f"{role}　房间 {self.code}　{mode}{left}", (170, 178, 192)))
            for i, p in enumerate(self.sorted_players()):
                mark = "▶" if p["id"] == self.my_id else "  "
                lines.append((f"{mark} {i + 1}. {self.name_of(p['id'])}"
                              f"　{int(p.get('s', 0)):,} 分　{p.get('m', 0)} 步",
                              (250, 236, 200) if p["id"] == self.my_id else (226, 232, 244)))
            foot = "X 断开联机　Esc 收起"

        w = 520
        h = 92 + len(lines) * 24
        card = pygame.Rect((g.W - w) // 2, max(24, (g.desk_h - h) // 2 - 30), w, h)
        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(panel, (16, 20, 28, 236), panel.get_rect(), border_radius=9)
        pygame.draw.rect(panel, (255, 255, 255, 44), panel.get_rect(),
                         width=1, border_radius=9)
        scr.blit(panel, card.topleft)

        g.blit_text(title, 14, (240, 244, 252), (card.x + 18, card.y + 14), shadow=0)
        y = card.y + 48
        for txt, col in lines:
            if txt:
                g.blit_text(txt, 11, col, (card.x + 20, y), bold=False, shadow=0)
            y += 24
        if foot:
            g.blit_text(foot, 10, (140, 148, 162), (card.x + 18, card.bottom - 24),
                        bold=False, shadow=0)

    # ---------------------------------------------------------- 底部条
    def draw_bar_slots(self, box, x0, ew, n=5):
        """底部条中间的 5 个格子：联机时改成实时比分，按分数排。"""
        g = self.game
        rows = self.sorted_players()[:n]
        for i in range(n):
            ex = int(x0 + ew * i)
            if i < len(rows):
                p = rows[i]
                mine = p["id"] == self.my_id
                top = i == 0
                col = ((250, 236, 200) if top else
                       ((236, 242, 252) if mine else (216, 224, 236)))
                g.blit_text(f"{i + 1}", 9,
                            (232, 196, 104) if top else (150, 158, 172),
                            (ex + 6, box.y + 12), bold=False, shadow=0)
                nm = self.name_of(p["id"])
                g.blit_text(nm[:5], 10, (168, 176, 190), (ex + 20, box.y + 11),
                            bold=False, shadow=0)
                g.blit_text(f"{int(p.get('s', 0)):,}", 15, col,
                            (ex + 20, box.y + 26), shadow=0)
            else:
                g.blit_text(f"{i + 1}", 9, (108, 114, 126), (ex + 6, box.y + 12),
                            bold=False, shadow=0)
                g.blit_text("—", 11, (96, 102, 114), (ex + 22, box.y + 12),
                            bold=False, shadow=0)

    def bar_title(self):
        mode = "同盘竞速" if self.mode == MODE_A else "共享棋盘"
        role = "房主" if self.role == "host" else "成员"
        return "联机 · " + mode, f"{role} · {len(self.players)} 人 · {self.code}"

    def bar_right(self):
        """底部条最右边那两块：A 模式显示倒计时，B 模式显示我的名次。"""
        if self.mode == MODE_A:
            return "剩余时间", _fmt_left(self.left)
        r = self.rank_of(self.my_id)
        return "我的名次", f"第 {r} 名" if r else "—"

    # ---------------------------------------------------------- 结算
    def standings(self):
        if self.game.net_over:
            return self.game.net_over.get("rows") or []
        return [{"id": p["id"], "n": self.name_of(p["id"]), "s": int(p.get("s", 0))}
                for p in self.sorted_players()]
