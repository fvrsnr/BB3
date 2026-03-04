## server_connect.py (fixed: connect to 10.0.0.1:5050; keyboard via keybd_event)
import socket
import struct
import threading
import time
from io import BytesIO

import mss
from PIL import Image

import ctypes
from ctypes import wintypes

# ---- Defaults for quick testing ----
CLIENT_IP = "16.145.50.24"
CLIENT_PORT = 443
FPS = 6
QUALITY = 35

# ---- ULONG_PTR compatibility ----
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# ---- Protocol helpers ----
def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed while receiving")
        buf.extend(chunk)
    return bytes(buf)

def recv_i32(sock: socket.socket) -> int:
    return struct.unpack("<i", recv_exact(sock, 4))[0]

def recv_u8(sock: socket.socket) -> int:
    return recv_exact(sock, 1)[0]

# ---- Win32 input injection ----
user32 = ctypes.WinDLL("user32", use_last_error=True)

user32.SetCursorPos.argtypes = [wintypes.INT, wintypes.INT]
user32.SetCursorPos.restype = wintypes.BOOL

user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ULONG_PTR]
user32.mouse_event.restype = None

# keybd_event is deprecated but very compatible
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ULONG_PTR]
user32.keybd_event.restype = None

MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP   = 0x0010
MOUSEEVENTF_WHEEL     = 0x0800

KEYEVENTF_KEYUP = 0x0002

def mouse_move(x: int, y: int):
    user32.SetCursorPos(int(x), int(y))

def mouse_button(button: int, down: bool, x: int, y: int):
    mouse_move(x, y)
    if button == 0:
        flag = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    elif button == 1:
        flag = MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
    else:
        return
    user32.mouse_event(flag, 0, 0, 0, ULONG_PTR(0))

def mouse_wheel(delta: int, x: int, y: int):
    mouse_move(x, y)
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, ctypes.c_uint32(delta).value, ULONG_PTR(0))

def key_event(vk: int, is_down: bool):
    flags = 0 if is_down else KEYEVENTF_KEYUP
    # bScan=0 lets Windows map scan from VK
    user32.keybd_event(wintypes.BYTE(vk & 0xFF), wintypes.BYTE(0), wintypes.DWORD(flags), ULONG_PTR(0))

# ---- Server capture ----
def capture_jpeg(quality: int) -> bytes:
    with mss.mss() as sct:
        mon = sct.monitors[1]  # primary monitor
        raw = sct.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=False)
        return buf.getvalue()

def input_reader(sock: socket.socket, stop_flag):
    sock.settimeout(0.2)
    saw_key = False

    while not stop_flag["stop"]:
        try:
            tag = sock.recv(1)
            if not tag:
                stop_flag["stop"] = True
                break

            t = tag[0]
            if t == ord("M"):
                x = recv_i32(sock); y = recv_i32(sock)
                mouse_move(x, y)
            elif t == ord("B"):
                btn = recv_u8(sock)
                x = recv_i32(sock); y = recv_i32(sock)
                mouse_button(btn, True, x, y)
            elif t == ord("U"):
                btn = recv_u8(sock)
                x = recv_i32(sock); y = recv_i32(sock)
                mouse_button(btn, False, x, y)
            elif t == ord("W"):
                delta = recv_i32(sock)
                x = recv_i32(sock); y = recv_i32(sock)
                mouse_wheel(delta, x, y)
            elif t == ord("K"):
                vk = recv_i32(sock)
                is_down = recv_u8(sock)
                if not saw_key:
                    print(f"Received first key event: vk={vk} down={is_down}")
                    saw_key = True
                key_event(vk, is_down == 1)
            else:
                pass

        except socket.timeout:
            continue
        except Exception as e:
            print(f"Input thread error: {e}")
            stop_flag["stop"] = True
            break

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    print(f"Connecting to client {CLIENT_IP}:{CLIENT_PORT} ...")
    sock.connect((CLIENT_IP, CLIENT_PORT))
    print("Connected.")

    sock.sendall(b"P")

    stop_flag = {"stop": False}
    threading.Thread(target=input_reader, args=(sock, stop_flag), daemon=True).start()

    frame_interval = 1.0 / max(1, FPS)
    sent = 0

    try:
        while not stop_flag["stop"]:
            jpg = capture_jpeg(QUALITY)
            sock.sendall(b"F" + struct.pack("<i", len(jpg)) + jpg)
            sent += 1
            if sent % 60 == 0:
                print(f"Sent frames: {sent} lastLen={len(jpg)}")
            time.sleep(frame_interval)
    finally:
        try:
            sock.close()
        except:
            pass

if __name__ == "__main__":
    main()



