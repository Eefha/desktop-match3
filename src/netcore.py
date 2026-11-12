# -*- coding: utf-8 -*-
"""
联机底层：帧协议 + 三条传输通道
--------------------------------
- TcpConn   ：局域网 / 公网（普通 socket，走 48000+ 端口）
- BthConn   ：蓝牙 RFCOMM（标准库 socket(AF_BTH) + ctypes 调 ws2_32，不依赖第三方库）
- MockConn  ：内存管道，单机跑两人的逻辑，跑测试用

帧格式统一：4 字节大端长度 + UTF-8 JSON。上层（netplay）只吃 dict，不关心走哪条线。
"""
from __future__ import annotations

import ctypes
import json
import queue
import socket
import struct
import threading
import time

PROTO_VER = 1
DISCOVERY_PORT = 47500
PORT_BASE = 48000          # 房间号 1000..9999 -> 端口 48000..56999
BT_CHANNEL = 7             # 蓝牙 RFCOMM 通道（1..30，固定一个省得扫）

HDR = 4
MAX_FRAME = 4 << 20


def pack(obj) -> bytes:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def _decode(buf: bytearray):
    """从缓冲区里尽量多地切出完整帧，返回 (frames, 剩下的字节)。"""
    out = []
    while len(buf) >= HDR:
        n = struct.unpack(">I", bytes(buf[:HDR]))[0]
        if n <= 0 or n > MAX_FRAME:
            buf.clear()
            break
        if len(buf) < HDR + n:
            break
        body = bytes(buf[HDR:HDR + n])
        del buf[:HDR + n]
        try:
            obj = json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


class Conn:
    """
    一条连接：后台线程收，主线程发和取。
    sock 只要有 recv/send/close/settimeout 就行 —— TCP socket 和蓝牙 RawSock 都符合。
    """

    def __init__(self, sock, peer=""):
        self.sock = sock
        self.peer = peer
        self.inbox: queue.Queue = queue.Queue()
        self.alive = True
        self.rtt = 0.0
        self.last_rx = time.time()      # 最后一次收到东西的时刻，用来判掉线
        self._buf = bytearray()
        self._wlock = threading.Lock()
        self._ping_t = 0.0
        try:
            sock.settimeout(0.4)
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._read_loop, daemon=True).start()

    # ---------------------------------------------------------- 收
    def _read_loop(self):
        while self.alive:
            try:
                data = self.sock.recv(65536)
            except (socket.timeout, TimeoutError):
                continue
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            self.last_rx = time.time()
            self._buf.extend(data)
            for obj in _decode(self._buf):
                self.inbox.put(obj)
        self.alive = False
        self.inbox.put({"t": "_closed"})

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out

    # ---------------------------------------------------------- 发
    def send(self, obj) -> bool:
        if not self.alive:
            return False
        data = pack(obj)
        try:
            with self._wlock:
                self.sock.sendall(data) if hasattr(self.sock, "sendall") else self._send_raw(data)
            return True
        except Exception:  # noqa: BLE001
            self.alive = False
            return False

    def _send_raw(self, data):
        mv = memoryview(data)
        while mv:
            n = self.sock.send(mv)
            if n <= 0:
                raise OSError("send failed")
            mv = mv[n:]

    def ping(self):
        self._ping_t = time.time()
        self.send({"t": "ping", "ts": self._ping_t})

    def pong(self, ts):
        self.send({"t": "pong", "ts": ts})

    def note_pong(self, ts):
        if ts:
            self.rtt = max(0.0, (time.time() - float(ts)) * 1000.0)

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


# ================================================================== 局域网 TCP
class TcpListener:
    def __init__(self, port: int):
        self.port = port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(8)
        s.settimeout(0.5)
        self.sock = s
        self.closed = False

    def accept(self, timeout=0.5):
        try:
            c, addr = self.sock.accept()
            return c, f"{addr[0]}"
        except (socket.timeout, TimeoutError):
            return None
        except Exception:  # noqa: BLE001
            return None

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


def tcp_connect(host: str, port: int, timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((host, port))
    s.settimeout(0.4)
    return s


def local_ip() -> str:
    """看看本机在局域网里的地址，只用于显示给对方看。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


# ================================================================== 房间广播
class Beacon:
    """房主每隔一会儿往局域网里喊一嗓子，别人开面板就能看见。"""

    def __init__(self, payload):
        self.payload = payload          # dict 或 返回 dict 的函数
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.closed = False
        self._last = 0.0

    def tick(self):
        now = time.time()
        if self.closed or now - self._last < 1.0:
            return
        self._last = now
        try:
            data = self.payload() if callable(self.payload) else self.payload
            self.sock.sendto(pack(data), ("255.255.255.255", DISCOVERY_PORT))
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class Scanner:
    """听 2.5 秒的广播，攒出一份房间列表（后台线程，不卡界面）。"""

    def __init__(self, seconds=2.5):
        self.rooms = {}
        self.done = False
        self.seconds = seconds
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("", DISCOVERY_PORT))
            s.settimeout(0.3)
            end = time.time() + self.seconds
            while time.time() < end:
                try:
                    data, addr = s.recvfrom(8192)
                except (socket.timeout, TimeoutError):
                    continue
                buf = bytearray(data)
                for obj in _decode(buf):
                    if obj.get("t") != "room":
                        continue
                    obj["ip"] = addr[0]
                    self.rooms[str(obj.get("code"))] = obj
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.done = True
            if s:
                try:
                    s.close()
                except Exception:  # noqa: BLE001
                    pass


# ================================================================== 蓝牙 RFCOMM
AF_BTH, BTPROTO_RFCOMM = 32, 3
SOCKET_ERROR = -1
WSAETIMEDOUT = 10060
WSAECONNREFUSED = 10061
WSAEHOSTUNREACH = 10065

_ws2 = None


def _ws():
    global _ws2
    if _ws2 is None:
        w = ctypes.WinDLL("ws2_32")
        w.WSAGetLastError.restype = ctypes.c_int
        for fn in ("bind", "listen", "connect", "closesocket", "getsockname",
                   "setsockopt"):
            getattr(w, fn).restype = ctypes.c_int
        w.accept.restype = ctypes.c_size_t
        w.send.restype = ctypes.c_int
        w.recv.restype = ctypes.c_int
        _ws2 = w
    return _ws2


class _BthAddr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("family", ctypes.c_ushort),
                ("btAddr", ctypes.c_ulonglong),
                ("serviceClassId", ctypes.c_ubyte * 16),
                ("port", ctypes.c_ulong)]


def bt_addr_to_int(s: str) -> int:
    """93:37:8C:71:88:00 -> int（Windows 按大端排）"""
    hexs = "".join(ch for ch in s if ch.hexdigits.find(ch) >= 0)
    return int(hexs, 16) if hexs else 0


def bt_int_to_addr(v: int) -> str:
    return ":".join(f"{(v >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


class RawSock:
    """裸 socket 句柄，接口对齐 Python socket 的那几个方法，Conn 能直接喂。"""

    def __init__(self, fd, peer=""):
        self.fd = ctypes.c_size_t(fd)
        self.peer = peer
        self.closed = False

    def settimeout(self, t):
        ms = ctypes.c_ulong(max(1, int(t * 1000)))
        _ws().setsockopt(self.fd, 0xFFFF, 0x1006,      # SOL_SOCKET, SO_RCVTIMEO
                         ctypes.byref(ms), ctypes.sizeof(ms))

    def recv(self, n):
        buf = ctypes.create_string_buffer(n)
        r = _ws().recv(self.fd, buf, n, 0)
        if r == SOCKET_ERROR:
            err = _ws().WSAGetLastError()
            if err == WSAETIMEDOUT:
                raise socket.timeout()
            raise OSError(err, "recv failed")
        return buf.raw[:r]

    def send(self, data):
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        r = _ws().send(self.fd, buf, len(data), 0)
        if r == SOCKET_ERROR:
            raise OSError(_ws().WSAGetLastError(), "send failed")
        return r

    def close(self):
        if not self.closed:
            self.closed = True
            _ws().closesocket(self.fd)


class BthListener:
    """蓝牙服务端：绑一个固定通道，等着别人连过来。"""

    def __init__(self, channel=BT_CHANNEL):
        self.channel = channel
        self.raw = socket.socket(AF_BTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
        sa = _BthAddr(family=AF_BTH, btAddr=0, port=channel)
        rc = _ws().bind(ctypes.c_size_t(self.raw.fileno()), ctypes.byref(sa),
                        ctypes.sizeof(sa))
        if rc != 0:
            err = _ws().WSAGetLastError()
            self.raw.close()
            raise OSError(err, "蓝牙端口绑定失败")
        if _ws().listen(ctypes.c_size_t(self.raw.fileno()), 4) != 0:
            err = _ws().WSAGetLastError()
            self.raw.close()
            raise OSError(err, "蓝牙 listen 失败")
        try:
            self.raw.settimeout(0.5)
        except Exception:  # noqa: BLE001
            pass
        self.addr = self._own_addr()
        self.closed = False

    def _own_addr(self):
        out = _BthAddr()
        n = ctypes.c_int(ctypes.sizeof(out))
        rc = _ws().getsockname(ctypes.c_size_t(self.raw.fileno()),
                               ctypes.byref(out), ctypes.byref(n))
        return bt_int_to_addr(out.btAddr) if rc == 0 else ""

    def accept(self, timeout=0.5):
        """ctypes 的 accept 在超时后不会回到 Python 的异常上，靠阻塞 + 线程外关掉。"""
        r, addr = None, None
        while not self.closed:
            fd = _ws().accept(ctypes.c_size_t(self.raw.fileno()), None, None)
            if fd == ctypes.c_size_t(-1).value or fd == 0xFFFFFFFFFFFFFFFF:
                err = _ws().WSAGetLastError()
                if err == 10035:                       # WSAEWOULDBLOCK
                    time.sleep(0.2)
                    continue
                return None
            sa = _BthAddr()
            n = ctypes.c_int(ctypes.sizeof(sa))
            peer = ""
            if _ws().getsockname(fd, ctypes.byref(sa), ctypes.byref(n)) == 0:
                peer = bt_int_to_addr(sa.btAddr)
            return RawSock(fd, peer)
        return None

    def close(self):
        self.closed = True
        try:
            self.raw.close()
        except Exception:  # noqa: BLE001
            pass


def bt_connect(addr: str, channel=BT_CHANNEL, timeout_ms=6000):
    raw = socket.socket(AF_BTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
    sa = _BthAddr(family=AF_BTH, btAddr=bt_addr_to_int(addr), port=channel)
    rc = _ws().connect(ctypes.c_size_t(raw.fileno()), ctypes.byref(sa),
                       ctypes.sizeof(sa))
    if rc != 0:
        err = _ws().WSAGetLastError()
        raw.close()
        if err == WSAECONNREFUSED:
            raise OSError(err, "对方没有开房（通道没监听），或者不在配对范围内")
        if err == WSAEHOSTUNREACH:
            raise OSError(err, "蓝牙连不上：先到系统设置里和设备配对一次")
        raise OSError(err, "蓝牙连接失败")
    return RawSock(raw.fileno(), addr)


# ================================================================== 内存管道
class MockConn:
    """单机测试用：两端各一半，写进去的从对面读出来。"""

    def __init__(self, peer="", delay=0.0):
        self.peer = peer
        self.inbox: queue.Queue = queue.Queue()
        self.alive = True
        self.rtt = 1.0
        self.delay = delay
        self._other = None

    @classmethod
    def pair(cls):
        a, b = cls("A"), cls("B")
        a._other, b._other = b, a
        return a, b

    def send(self, obj) -> bool:
        if self._other is None or not self._other.alive:
            return False
        self._other.inbox.put(json.loads(json.dumps(obj)))
        return True

    def _send_raw(self, data):
        raise NotImplementedError

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out

    def ping(self):
        self.send({"t": "ping", "ts": time.time()})

    def pong(self, ts):
        self.send({"t": "pong", "ts": ts})

    def note_pong(self, ts):
        self.rtt = 1.0

    def close(self):
        self.alive = False
