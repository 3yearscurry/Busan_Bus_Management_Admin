import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
import re
import tempfile
from tkinter import messagebox

if os.name == "nt":
    import ctypes
    import winreg
    from ctypes import wintypes
else:
    ctypes = None
    winreg = None
    wintypes = None

if ctypes is not None:
    _LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
else:
    _LRESULT = None

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENV_SITE_PACKAGES = os.path.join(_ROOT_DIR, ".venv", "Lib", "site-packages")
if os.path.isdir(_VENV_SITE_PACKAGES) and _VENV_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_SITE_PACKAGES)

import customtkinter as ctk
from bus_api import BusanBusAPI
import updater

_ICON_CACHE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "부산실시간버스안내", "assets")


def _create_icon(target_path):
    from PIL import Image, ImageDraw
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    magenta = (226, 0, 137)
    pink = (229, 63, 163)
    violet = (87, 45, 145)
    purple = (154, 18, 151)
    blue = (0, 118, 188)

    # 부산광역시 심볼을 작은 앱 아이콘에서도 보이도록 단순화한 형태.
    draw.polygon([(48, 42), (164, 42), (220, 98), (158, 160), (48, 160)], fill=magenta)
    draw.polygon([(48, 42), (164, 42), (190, 68), (48, 98)], fill=(214, 0, 132))
    draw.polygon([(48, 98), (158, 160), (220, 98), (115, 78)], fill=pink)
    draw.polygon([(48, 98), (48, 160), (115, 145)], fill=purple)
    draw.polygon([(48, 160), (158, 160), (220, 98), (115, 145)], fill=violet)
    draw.polygon([(48, 160), (111, 147), (88, 160)], fill=blue)

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    img.save(target_path, format="ICO",
             sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])


def _resolve_icon_path():
    candidates = []

    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "icon.ico"))
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.join(meipass, "icon.ico"))

    candidates.append(os.path.join(_ROOT_DIR, "icon.ico"))

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    generated_path = os.path.join(_ICON_CACHE_DIR, "icon.ico")
    try:
        _create_icon(generated_path)
        return generated_path
    except Exception:
        return ""


_ICON_PATH = _resolve_icon_path()

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

FONT = "맑은 고딕"

BUS_COLORS = {
    "normal": "#3d74db",
    "express": "#e03030",
    "village": "#5aad46",
    "night": "#8b4ebe",
    "airport": "#8b4ebe",
}

CONG_COLORS = {
    "여유":   "#2ecc71",
    "보통":   "#f39c12",
    "혼잡":   "#e67e22",
    "매우혼잡": "#e74c3c",
}

TTS_ALERT_MINUTES = (10, 5, 3, 0)
TTS_VOICE = "ko-KR-SunHiNeural"
TTS_RATE = "-5%"
_TTS_LOCK = threading.Lock()

_SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "부산실시간버스안내")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "user_settings.json")
_STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_VALUE_NAME = "BusanRealtimeBusInfo"
_DEFAULT_SETTINGS = {
    "refresh_sec": 30,
    "tts_voice": TTS_VOICE,
    "tts_rate": TTS_RATE,
    "background_on_close": None,
    "run_on_startup": True,
    "show_whats_new": True,
}


def _load_settings():
    settings = dict(_DEFAULT_SETTINGS)
    try:
        with open(_SETTINGS_FILE, encoding="utf-8") as fp:
            raw = json.load(fp)
        if isinstance(raw, dict):
            settings.update(raw)
    except Exception:
        pass
    startup_enabled = _is_run_on_startup_enabled()
    if startup_enabled is not None:
        settings["run_on_startup"] = startup_enabled
    return settings


def _save_settings(settings):
    try:
        os.makedirs(_SETTINGS_DIR, exist_ok=True)
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as fp:
            json.dump(settings, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    python_exe = sys.executable
    script_path = os.path.abspath(__file__)
    base, name = os.path.split(python_exe)
    if name.lower() == "python.exe":
        pythonw = os.path.join(base, "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
    return f'"{python_exe}" "{script_path}"'


def _set_run_on_startup(enabled):
    if os.name != "nt" or winreg is None:
        return False, "이 기능은 Windows에서만 사용할 수 있습니다."

    command = _startup_command()

    if enabled:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, _STARTUP_VALUE_NAME, 0, winreg.REG_SZ, command)
            return True, ""
        except Exception as ex:
            return False, str(ex)

    removed = False
    permission_error = False
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, _STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, _STARTUP_VALUE_NAME)
                    removed = True
                except FileNotFoundError:
                    pass
        except PermissionError:
            permission_error = True
        except Exception:
            pass

    if removed or not permission_error:
        return True, ""
    return False, "관리자 권한으로 설치된 시작 프로그램은 관리자 권한이 있어야 해제할 수 있습니다."


def _startup_value_roots():
    if os.name != "nt" or winreg is None:
        return []
    return [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]


def _is_run_on_startup_enabled():
    if os.name != "nt" or winreg is None:
        return None
    command = _startup_command()
    found_any = False
    for root in _startup_value_roots():
        try:
            with winreg.OpenKey(root, _STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, _STARTUP_VALUE_NAME)
            found_any = True
            if str(value or "").strip() == command:
                return True
        except FileNotFoundError:
            continue
        except PermissionError:
            found_any = True
        except Exception:
            continue
    return False if found_any else None


def _speak_async(text, on_done=None):
    def worker():
        ok = _speak_text(text)
        if on_done:
            on_done(ok)

    threading.Thread(target=worker, daemon=True).start()


def _speak_text(text):
    with _TTS_LOCK:
        return _speak_edge_neural(text)


def _speak_edge_neural(text):
    try:
        import edge_tts
    except Exception:
        return False

    media_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
            media_path = fp.name

        communicate = edge_tts.Communicate(
            str(text or ""),
            voice=TTS_VOICE,
            rate=TTS_RATE,
            pitch="+0Hz",
        )
        asyncio.run(communicate.save(media_path))
        return _play_media_file(media_path)
    except Exception:
        return False
    finally:
        if media_path:
            try:
                os.remove(media_path)
            except Exception:
                pass


def _play_media_file(path):
    try:
        import ctypes

        winmm = ctypes.WinDLL("winmm")
        alias = f"tts_{threading.get_ident()}"

        def mci(command):
            buffer = ctypes.create_unicode_buffer(256)
            code = winmm.mciSendStringW(command, buffer, len(buffer), None)
            return code, buffer.value

        media_path = os.path.abspath(path)
        mci(f'close {alias}')
        code, _ = mci(f'open "{media_path}" type mpegvideo alias {alias}')
        if code:
            return False

        code, _ = mci(f'play {alias} wait')
        mci(f'close {alias}')
        return code == 0
    except Exception:
        return False


def _apply_window_icon(window):
    if not _ICON_PATH:
        return
    try:
        window.iconbitmap(_ICON_PATH)
    except Exception:
        pass


class WindowsTrayIcon:
    WM_TRAYICON = 0x8001
    WM_COMMAND = 0x0111
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    WM_CONTEXTMENU = 0x007B

    NIM_ADD = 0x00000000
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004

    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010
    LR_DEFAULTSIZE = 0x00000040
    IDI_APPLICATION = 32512

    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    TPM_LEFTALIGN = 0x0000
    TPM_BOTTOMALIGN = 0x0020
    TPM_RIGHTBUTTON = 0x0002

    MENU_OPEN = 1001
    MENU_SETTINGS = 1002
    MENU_EXIT = 1003

    def __init__(self, on_open, on_settings, on_exit, tooltip):
        self._on_open = on_open
        self._on_settings = on_settings
        self._on_exit = on_exit
        self._tooltip = tooltip[:127]
        self._thread = None
        self._hwnd = None
        self._class_name = None
        self._wndproc = None
        self._notify_data = None
        self._hicon = None
        self._owns_hicon = False

    def start(self):
        if os.name != "nt" or ctypes is None:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        for _ in range(40):
            if self._hwnd:
                return True
            if not self._thread.is_alive():
                return False
            threading.Event().wait(0.02)
        return bool(self._hwnd)

    def stop(self):
        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, self.WM_CLOSE, 0, 0)
        if self._thread and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def _dispatch(self, callback):
        try:
            callback()
        except Exception:
            pass

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = _LRESULT
        wndproc_type = ctypes.WINFUNCTYPE(
            _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HCURSOR),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON),
            ]

        self._NOTIFYICONDATAW = NOTIFYICONDATAW
        self._wndproc = wndproc_type(self._window_proc)

        hinstance = kernel32.GetModuleHandleW(None)
        self._class_name = f"BusanBusTrayWindow_{id(self)}"

        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = self._class_name
        wndclass.hCursor = user32.LoadCursorW(None, 32512)

        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom and kernel32.GetLastError() != 1410:
            return

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            user32.UnregisterClassW(self._class_name, hinstance)
            return

        self._hwnd = hwnd
        self._add_icon()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._remove_icon()
        user32.UnregisterClassW(self._class_name, hinstance)
        self._hwnd = None

    def _load_icon(self):
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        if _ICON_PATH and os.path.exists(_ICON_PATH):
            icon = user32.LoadImageW(
                None,
                _ICON_PATH,
                self.IMAGE_ICON,
                0,
                0,
                self.LR_LOADFROMFILE | self.LR_DEFAULTSIZE,
            )
            if icon:
                self._owns_hicon = True
                return icon
        if getattr(sys, "frozen", False):
            exe_icon = shell32.ExtractIconW(None, sys.executable, 0)
            if exe_icon and exe_icon > 1:
                self._owns_hicon = True
                return exe_icon
        self._owns_hicon = False
        return user32.LoadIconW(None, self.IDI_APPLICATION)

    def _add_icon(self):
        shell32 = ctypes.windll.shell32
        self._hicon = self._load_icon()
        notify_data = self._NOTIFYICONDATAW()
        notify_data.cbSize = ctypes.sizeof(self._NOTIFYICONDATAW)
        notify_data.hWnd = self._hwnd
        notify_data.uID = 1
        notify_data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        notify_data.uCallbackMessage = self.WM_TRAYICON
        notify_data.hIcon = self._hicon
        notify_data.szTip = self._tooltip
        shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(notify_data))
        self._notify_data = notify_data

    def _remove_icon(self):
        if not self._notify_data:
            return
        shell32 = ctypes.windll.shell32
        shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(self._notify_data))
        if self._hicon and self._owns_hicon:
            ctypes.windll.user32.DestroyIcon(self._hicon)
        self._notify_data = None
        self._hicon = None
        self._owns_hicon = False

    def _show_menu(self):
        if not self._hwnd:
            return
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            user32.AppendMenuW(menu, self.MF_STRING, self.MENU_OPEN, "열기")
            user32.AppendMenuW(menu, self.MF_STRING, self.MENU_SETTINGS, "설정")
            user32.AppendMenuW(menu, self.MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, self.MF_STRING, self.MENU_EXIT, "종료")
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self._hwnd)
            user32.TrackPopupMenu(
                menu,
                self.TPM_LEFTALIGN | self.TPM_BOTTOMALIGN | self.TPM_RIGHTBUTTON,
                point.x,
                point.y,
                0,
                self._hwnd,
                None,
            )
        finally:
            user32.DestroyMenu(menu)

    def _window_proc(self, hwnd, msg, wparam, lparam):
        user32 = ctypes.windll.user32
        if msg == self.WM_TRAYICON:
            if lparam == self.WM_LBUTTONDBLCLK:
                self._dispatch(self._on_open)
                return 0
            if lparam in (self.WM_LBUTTONUP, self.WM_RBUTTONUP, self.WM_CONTEXTMENU):
                self._show_menu()
                return 0
        elif msg == self.WM_COMMAND:
            command = int(wparam) & 0xFFFF
            if command == self.MENU_OPEN:
                self._dispatch(self._on_open)
                return 0
            if command == self.MENU_SETTINGS:
                self._dispatch(self._on_settings)
                return 0
            if command == self.MENU_EXIT:
                self._dispatch(self._on_exit)
                return 0
        elif msg == self.WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == self.WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# ─────────────────────────────────────────────────────────────
# 버스 카드 위젯
# ─────────────────────────────────────────────────────────────
class BusCard(ctk.CTkFrame):
    def __init__(self, parent, data, on_number_click=None, on_alert_click=None, alert_active=False, **kwargs):
        super().__init__(parent, corner_radius=8, fg_color="white",
                         border_width=1, border_color="#e0e0e0", **kwargs)
        self._on_number_click = on_number_click
        self._on_alert_click = on_alert_click
        self._alert_active = alert_active
        self._build(data)

    def _build(self, d):
        color = BUS_COLORS.get(d.get("type", "3"), "#3d74db")

        # 왼쪽 색상 바
        bar = ctk.CTkFrame(self, width=7, fg_color=color, corner_radius=0)
        bar.pack(side="left", fill="y")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=12, pady=10)

        # ── 상단: 버스 번호 + 타입 뱃지 + 혼잡도
        top = ctk.CTkFrame(body, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkButton(top, text=d["number"], width=78, height=30,
                  font=ctk.CTkFont(FONT, 22, "bold"),
                  fg_color="#f5f8ff", hover_color="#e8f0ff",
                  text_color=color, border_width=0,
                  command=self._handle_number_click).pack(side="left")

        ctk.CTkLabel(top, text=f" {d.get('type_name', '?')} ",
                     font=ctk.CTkFont(FONT, 11),
                     text_color="white", fg_color=color,
                     corner_radius=4).pack(side="left", padx=(6, 0), pady=(4, 0))

        cong = d.get("congestion", "")
        if cong:
            ctk.CTkLabel(top, text=cong,
                         font=ctk.CTkFont(FONT, 11, "bold"),
                         text_color=CONG_COLORS.get(cong, "#888"),
                         fg_color="transparent").pack(side="right")

        if self._on_alert_click:
            ctk.CTkButton(
                top,
                text="🔔",
                width=34,
                height=28,
                font=ctk.CTkFont(FONT, 15),
                fg_color="#fff3cd" if self._alert_active else "#f0f2f6",
                hover_color="#ffe8a1",
                text_color="#8a5a00" if self._alert_active else "#666",
                command=self._handle_alert_click,
            ).pack(side="right", padx=(6, 0))

        # ── 노선 (출발 → 도착)
        ctk.CTkLabel(body,
                     text=f"{d.get('start', '?')}  →  {d.get('end', '?')}",
                     font=ctk.CTkFont(FONT, 13), text_color="#666").pack(anchor="w", pady=(2, 0))

        # ── 도착 시간 또는 노선 요약
        arr_row = ctk.CTkFrame(body, fg_color="transparent")
        arr_row.pack(anchor="w", pady=(8, 0))

        arr1 = d.get("arrival1")
        arr2 = d.get("arrival2")
        is_route_summary = arr1 is None and arr2 is None and d.get("term")

        if is_route_summary:
            ctk.CTkLabel(arr_row, text="배차",
                         font=ctk.CTkFont(FONT, 11), text_color="#aaa").pack(side="left")
            ctk.CTkLabel(arr_row, text=f"  {d.get('term')}분",
                         font=ctk.CTkFont(FONT, 16, "bold"),
                         text_color=color).pack(side="left")
        else:
            arr1_txt = "곧 도착" if arr1 is not None and arr1 <= 1 else (f"{arr1}분 후" if arr1 is not None else "정보없음")
            arr2_txt  = f"{arr2}분 후" if arr2 is not None else "—"

            ctk.CTkLabel(arr_row, text="다음",
                         font=ctk.CTkFont(FONT, 11), text_color="#aaa").pack(side="left")
            ctk.CTkLabel(arr_row, text=f"  {arr1_txt}",
                         font=ctk.CTkFont(FONT, 16, "bold"),
                         text_color=color if arr1 is not None else "#ccc").pack(side="left")

            ctk.CTkLabel(arr_row, text="    그 다음",
                         font=ctk.CTkFont(FONT, 11), text_color="#aaa").pack(side="left")
            ctk.CTkLabel(arr_row, text=f"  {arr2_txt}",
                         font=ctk.CTkFont(FONT, 14), text_color="#999").pack(side="left")

        if d.get("buses_running") is not None:
            ctk.CTkLabel(body, text=f"운행 중 {d['buses_running']}대",
                         font=ctk.CTkFont(FONT, 11), text_color="#bbb").pack(anchor="w", pady=(4, 0))

    def _handle_number_click(self):
        if self._on_number_click:
            self._on_number_click()

    def _handle_alert_click(self):
        if self._on_alert_click:
            self._on_alert_click()


# ─────────────────────────────────────────────────────────────
# 정류소 도착 정보 팝업
# ─────────────────────────────────────────────────────────────
class ArrivalWindow(ctk.CTkToplevel):
    def __init__(self, parent, station, arrivals):
        super().__init__(parent)
        self.parent = parent
        self.station = station
        self._alive = True
        self._countdown = 0
        self._last_updated = None
        self._last_arrivals = arrivals
        self._tts_alerts = {}

        station_name = station.get("name", "") if isinstance(station, dict) else str(station)
        self.title(f"도착 정보 — {station_name}")
        _apply_window_icon(self)
        self.geometry("480x560")
        self.resizable(False, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.transient(parent)
        self.after(50, lambda: (self.lift(), self.focus_force()))
        if hasattr(parent, "_register_managed_window"):
            parent._register_managed_window(self)

        ctk.CTkLabel(self, text=f"🚏  {station_name}",
                     font=ctk.CTkFont(FONT, 17, "bold"),
                     text_color="#003594").pack(pady=(18, 4), padx=16, anchor="w")

        self.lbl_status = ctk.CTkLabel(self, text="",
                                       font=ctk.CTkFont(FONT, 12), text_color="#aaa")
        self.lbl_status.pack(padx=16, anchor="w")

        sep = ctk.CTkFrame(self, height=1, fg_color="#ddd")
        sep.pack(fill="x", padx=16, pady=10)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#f5f7fa")
        self.scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._render(arrivals)
        self._countdown = parent.REFRESH_SEC
        self._update_refresh_status()
        self.after(1000, self._tick)

    def _render(self, arrivals, update_stamp=True, check_alerts=True):
        for w in self.scroll.winfo_children():
            w.destroy()
        self._last_arrivals = arrivals
        if update_stamp:
            self._last_updated = datetime.now()
        self._update_refresh_status()
        if not arrivals:
            ctk.CTkLabel(self.scroll, text="도착 정보가 없습니다.",
                         font=ctk.CTkFont(FONT, 14), text_color="#888").pack(pady=30)
            return
        for bus in arrivals:
            route_key = self._route_alert_key(bus)
            BusCard(
                self.scroll,
                bus,
                on_number_click=lambda b=bus, s=self.station: self.parent._show_route_stops(b, focus_station=s),
                on_alert_click=lambda b=bus: self._toggle_tts_alert(b),
                alert_active=route_key in self._tts_alerts,
            ).pack(fill="x", pady=4)
        if check_alerts:
            self._check_tts_alerts(arrivals)

    def _tick(self):
        if not self._alive:
            return
        self._countdown -= 1
        if self._countdown <= 0:
            self.lbl_status.configure(text="갱신 중...")
            self._refresh()
        else:
            self._update_refresh_status()
            self.after(1000, self._tick)

    def _refresh(self):
        if not self._alive:
            return
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        try:
            arrivals = self.parent.api.get_arrivals(self.station)
            self.after(0, lambda: self._apply_refresh(arrivals))
        except Exception as ex:
            error = str(ex)
            self.after(0, lambda: self.parent._show_api_status(False))
            self.after(0, lambda: self.lbl_status.configure(text="갱신 실패 · 다음 갱신을 기다리는 중"))
            self.after(0, lambda: self.parent._set_status(self.parent._format_error_status("도착 정보 갱신 실패", error)))

    def _apply_refresh(self, arrivals):
        if not self._alive:
            return
        self._render(arrivals)
        self._countdown = self.parent.REFRESH_SEC
        self._update_refresh_status()
        self.after(1000, self._tick)

    def _route_alert_key(self, bus):
        return str(bus.get("route_id") or bus.get("id") or bus.get("number") or "")

    def _toggle_tts_alert(self, bus):
        key = self._route_alert_key(bus)
        if not key:
            return

        if key in self._tts_alerts:
            del self._tts_alerts[key]
            self.lbl_status.configure(text=f"{bus.get('number', '')}번 음성 알림 해제")
            self._render(self._last_arrivals, update_stamp=False, check_alerts=False)
            self._show_tts_toast(bus.get("number", ""), "음성 알림이 해제되었습니다.")
            return

        self._tts_alerts[key] = {
            "number": str(bus.get("number", "")),
            "announced": set(),
        }
        self.lbl_status.configure(text=f"{bus.get('number', '')}번 음성 알림 설정")
        self._render(self._last_arrivals, update_stamp=False, check_alerts=False)
        self._show_tts_toast(bus.get("number", ""), "음성 알림 기능을 사용합니다.")
        _speak_async("음성 안내를 시작합니다.", on_done=lambda ok: self.after(0, lambda: self._apply_tts_start_result(ok)))
        if not self._announce_if_due(bus, force=True):
            self.lbl_status.configure(text=f"{bus.get('number', '')}번 음성 알림 설정 · 10분/5분/3분/곧 도착 시 안내")

    def _check_tts_alerts(self, arrivals):
        self._last_arrivals = arrivals
        for bus in arrivals:
            self._announce_if_due(bus)

    def _announce_if_due(self, bus, force=False):
        key = self._route_alert_key(bus)
        if key not in self._tts_alerts:
            return False

        minute = bus.get("arrival1")
        if minute is None:
            return False

        threshold = self._tts_threshold(minute)
        if threshold is None:
            return False

        alert = self._tts_alerts[key]
        if threshold in alert["announced"] and not force:
            return False

        alert["announced"].add(threshold)
        number = alert.get("number") or bus.get("number", "")
        if threshold == 0:
            message = f"{number}번 버스가 잠시 후 도착합니다."
        else:
            message = f"{number}번 버스가 {threshold}분 후에 도착합니다."
        self._show_tts_toast(number, self._format_tts_toast_message(bus, threshold))
        _speak_async(message, on_done=lambda ok: self.after(0, lambda: self._apply_tts_result(number, ok)))
        return True

    def _format_tts_toast_message(self, bus, threshold):
        if threshold == 0:
            headline = "곧 도착"
        else:
            headline = f"{threshold}분 남음"

        stations_away = bus.get("arrival1_stations")
        try:
            stations_away = int(stations_away)
        except (TypeError, ValueError):
            stations_away = None

        if stations_away and stations_away > 0:
            return f"{headline} ({stations_away}정거장 전)"
        return headline

    def _show_tts_toast(self, number, message):
        title = "음성 알림"
        number = str(number or "").strip()
        if number:
            title = f"{number}번 음성 알림"
        updater.show_toast(title, message)

    def _apply_tts_result(self, number, ok):
        if not self._alive:
            return
        if ok:
            self.lbl_status.configure(text=f"{number}번 음성 안내 재생 완료")
        else:
            self.lbl_status.configure(text="음성 안내 재생 실패 · 인터넷 연결 또는 오디오 장치를 확인해주세요")

    def _apply_tts_start_result(self, ok):
        if not self._alive:
            return
        if not ok:
            self.lbl_status.configure(text="음성 안내 시작 실패 · 인터넷 연결 또는 오디오 장치를 확인해주세요")

    def _tts_threshold(self, minute):
        if minute <= 1:
            return 0
        for threshold in sorted(t for t in TTS_ALERT_MINUTES if t > 0):
            if minute <= threshold:
                return threshold
        return None

    def _update_refresh_status(self):
        if not self._last_updated:
            self.lbl_status.configure(text="갱신 준비 중...")
            return
        stamp = self._last_updated.strftime("%Y-%m-%d %H:%M:%S")
        self.lbl_status.configure(text=f"마지막 갱신: {stamp} · 갱신까지 {self._countdown}초")

    def _on_close(self):
        self._alive = False
        if hasattr(self.parent, "_unregister_managed_window"):
            self.parent._unregister_managed_window(self)
        self.destroy()


# ─────────────────────────────────────────────────────────────
# 경유 정류장 팝업
# ─────────────────────────────────────────────────────────────
class RouteStopsWindow(ctk.CTkToplevel):
    def __init__(self, parent, route, stops, focus_station=None):
        super().__init__(parent)
        self.parent = parent
        self.route = route
        self.stops = stops
        self.focus_station = focus_station
        self._row_widgets = []
        self._alive = True
        self._countdown = 0
        self._last_updated = None
        self._route_color = BUS_COLORS.get(route.get("type", "3"), "#3d74db")

        self.title(f"경유 정류장 — {route['number']}")
        _apply_window_icon(self)
        self.geometry("540x700")
        self.minsize(480, 520)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.transient(parent)
        self.after(50, lambda: (self.lift(), self.focus_force()))

        self.configure(fg_color="#f2f3f5")

        top = ctk.CTkFrame(self, fg_color="white", corner_radius=0, height=78)
        top.pack(fill="x")
        top.pack_propagate(False)

        top_left = ctk.CTkFrame(top, fg_color="transparent")
        top_left.pack(side="left", padx=16, pady=14, fill="y")

        route_badge = ctk.CTkLabel(
            top_left,
            text=route.get("type_name", "노선"),
            font=ctk.CTkFont(FONT, 11, "bold"),
            text_color="white",
            fg_color=self._route_color,
            corner_radius=7,
            padx=8,
            pady=2,
        )
        route_badge.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            top_left,
            text=str(route.get("number", "")),
            font=ctk.CTkFont(FONT, 26, "bold"),
            text_color="#171717",
        ).pack(side="left")

        ctk.CTkButton(
            top,
            text="새로고침",
            width=72,
            height=30,
            corner_radius=16,
            fg_color="#f0f2f6",
            hover_color="#e5e8ed",
            text_color="#444",
            command=self._refresh_arrivals,
        ).pack(side="right", padx=16, pady=20)

        summary = f"{route.get('start', '?')} -> {route.get('end', '?')}"
        ctk.CTkLabel(
            self,
            text=summary,
            font=ctk.CTkFont(FONT, 12),
            text_color="#666",
            fg_color="#f2f3f5",
        ).pack(padx=18, pady=(8, 0), anchor="w")

        ctk.CTkLabel(
            self,
            text=f"총 {len(stops)}개 정류장 · 운행 중",
            font=ctk.CTkFont(FONT, 12),
            text_color="#555",
            fg_color="#f2f3f5",
        ).pack(padx=18, pady=(1, 6), anchor="w")

        if self.focus_station:
            ctk.CTkLabel(
                self,
                text=f"검색 기준 정류장: {self.focus_station.get('name', '')}",
                font=ctk.CTkFont(FONT, 11, "bold"),
                text_color="#b35a00",
                fg_color="#f2f3f5",
            ).pack(padx=18, pady=(0, 6), anchor="w")

        self.lbl_live = ctk.CTkLabel(
            self,
            text="갱신 준비 중...",
            font=ctk.CTkFont(FONT, 11),
            text_color="#555",
            fg_color="#f2f3f5",
        )
        self.lbl_live.pack(padx=18, pady=(0, 8), anchor="w")

        sep = ctk.CTkFrame(self, height=1, fg_color="#d9dde3")
        sep.pack(fill="x", padx=18, pady=(0, 10))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#f2f3f5")
        self.scroll.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        if not stops:
            ctk.CTkLabel(self.scroll, text="경유 정류장 정보를 찾지 못했습니다.",
                         font=ctk.CTkFont(FONT, 14), text_color="#888").pack(pady=30)
            return

        for idx, stop in enumerate(stops, start=1):
            row = ctk.CTkFrame(self.scroll, fg_color="transparent")
            row.pack(fill="x", padx=2, pady=2)

            lane = ctk.CTkFrame(row, width=36, fg_color="transparent")
            lane.pack(side="left", fill="y", padx=(4, 8), pady=2)

            top_line_color = "#c6c9cf" if idx != 1 else "transparent"
            bottom_line_color = "#c6c9cf" if idx != len(stops) else "transparent"

            top_line = ctk.CTkFrame(lane, width=4, height=16, fg_color=top_line_color, corner_radius=2)
            top_line.pack(pady=(0, 1))

            node = ctk.CTkLabel(
                lane,
                text="○",
                width=24,
                font=ctk.CTkFont(FONT, 16, "bold"),
                text_color="#8a8a8a",
                fg_color="transparent",
            )
            node.pack()

            bottom_line = ctk.CTkFrame(lane, width=4, height=16, fg_color=bottom_line_color, corner_radius=2)
            bottom_line.pack(pady=(1, 0), fill="y", expand=True)

            body = ctk.CTkFrame(
                row,
                fg_color="white",
                corner_radius=10,
                border_width=0,
            )
            body.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=2)

            body_inner = ctk.CTkFrame(body, fg_color="transparent")
            body_inner.pack(fill="x", padx=12, pady=10)

            ctk.CTkLabel(
                body_inner,
                text=stop["name"],
                font=ctk.CTkFont(FONT, 15, "bold"),
                text_color="#222",
            ).pack(anchor="w")

            direction = route.get("end") or "방면 정보 없음"
            ars_id = str(stop.get("arsId", "")).strip()
            meta_txt = f"{direction} 방면 ({ars_id})" if ars_id else f"{direction} 방면"
            ctk.CTkLabel(
                body_inner,
                text=meta_txt,
                font=ctk.CTkFont(FONT, 11),
                text_color="#9a9a9a",
            ).pack(anchor="w", pady=(1, 0))

            eta = ctk.CTkLabel(
                body_inner,
                text="정보없음",
                font=ctk.CTkFont(FONT, 13, "bold"),
                text_color="#8c8c8c",
            )
            eta.pack(anchor="w", pady=(6, 0))

            eta_sub = ctk.CTkLabel(
                body_inner,
                text="",
                font=ctk.CTkFont(FONT, 12),
                text_color="#8c8c8c",
            )
            eta_sub.pack(anchor="w", pady=(1, 0))

            keys = []
            for candidate in [stop.get("id"), stop.get("name"), stop.get("arsId")]:
                if candidate and candidate not in keys:
                    keys.append(candidate)

            self._row_widgets.append({
                "row": row,
                "body": body,
                "eta": eta,
                "eta_sub": eta_sub,
                "stop": stop,
                "keys": keys,
                "line_top": top_line,
                "line_bottom": bottom_line,
                "node": node,
                "index": idx - 1,
                "default_body": "white",
            })

        self._refresh_arrivals()

    def _tick(self):
        if not self._alive:
            return
        self._countdown -= 1
        if self._countdown <= 0:
            self.lbl_live.configure(text="갱신 중...")
            self._refresh_arrivals()
        else:
            self._update_refresh_status()
            self.after(1000, self._tick)

    def _on_close(self):
        self._alive = False
        if hasattr(self.parent, "_unregister_managed_window"):
            self.parent._unregister_managed_window(self)
        self.destroy()

    def _refresh_arrivals(self):
        if not self._alive:
            return
        threading.Thread(target=self._do_refresh_arrivals, daemon=True).start()

    def _do_refresh_arrivals(self):
        try:
            arrivals = self.parent.api.get_route_station_arrivals(
                self.route.get("id", ""),
                self.route.get("number", ""),
                self.stops,
                self.focus_station,
            )
            self.after(0, lambda: self._apply_arrivals(arrivals))
        except Exception as ex:
            error = str(ex)
            self.after(0, lambda: self.parent._show_api_status(False))
            self.after(0, lambda: self.lbl_live.configure(text="갱신 실패 · 다음 갱신을 기다리는 중"))
            self.after(0, lambda: self.parent._set_status(self.parent._format_error_status("노선 정보 갱신 실패", error)))

    def _apply_arrivals(self, arrivals):
        if not self._alive:
            return

        current_idx = None
        current_best = None
        fallback_idx = None

        for idx, refs in enumerate(self._row_widgets):
            info = self._find_arrival_info(arrivals, refs["keys"])
            refs["info"] = info
            if not info:
                continue

            if fallback_idx is None and (info.get("arrmsg1") or info.get("arrmsg2") or info.get("position_only")):
                fallback_idx = idx

            a1 = info.get("arrival1")
            if isinstance(a1, int):
                if current_best is None or a1 < current_best:
                    current_best = a1
                    current_idx = idx

        if current_idx is None:
            current_idx = fallback_idx

        focus_idx = None

        for idx, refs in enumerate(self._row_widgets):
            info = refs.get("info")
            is_focus = self._is_focus_stop(refs["stop"])
            if is_focus:
                focus_idx = idx
            is_current = current_idx is not None and idx == current_idx
            line_top, line_bottom, node_color = self._line_state(idx, current_idx, len(self._row_widgets), is_focus, is_current)

            refs["line_top"].configure(fg_color=line_top)
            refs["line_bottom"].configure(fg_color=line_bottom)

            if info and info.get("position_only"):
                refs["node"].configure(text="🚌", text_color=self._route_color)
            elif is_current and is_focus:
                refs["node"].configure(text="●", text_color="#7d3c98")
            elif is_current:
                refs["node"].configure(text="●", text_color=node_color)
            elif is_focus:
                refs["node"].configure(text="◎", text_color="#b35a00")
            else:
                refs["node"].configure(text="○", text_color=node_color)

            focus_body_color = "#e4f0ff"
            if info is None:
                body_color = focus_body_color if is_focus else ("#eef2f7" if is_current else refs["default_body"])
                refs["body"].configure(fg_color=body_color)
                refs["eta"].configure(text="도착예정 없음", text_color="#0b4ea2" if is_focus else "#9a9a9a")
                refs["eta_sub"].configure(text="", text_color="#8c8c8c")
                continue

            if info.get("position_only"):
                body_color = focus_body_color if is_focus else ("#eef2f7" if is_current else refs["default_body"])
                refs["body"].configure(fg_color=body_color)
                refs["eta"].configure(text="곧 도착", text_color=self._route_color)
                next_txt = self._arrival_text(info, "arrival2", "arrival2_stations")
                refs["eta_sub"].configure(
                    text=f"다음 {next_txt}" if next_txt != "정보없음" else "",
                    text_color="#8c8c8c",
                )
                continue

            a1 = info.get("arrival1")
            a2 = info.get("arrival2")
            a1_txt = self._arrival_text(info, "arrival1", "arrival1_stations")
            a2_txt = self._arrival_text(info, "arrival2", "arrival2_stations")

            body_color = focus_body_color if is_focus else ("#e9edf2" if is_current else refs["default_body"])
            refs["body"].configure(fg_color=body_color)

            if is_focus and current_idx is not None:
                refs["eta"].configure(text=a1_txt, text_color="#d63a33")
                refs["eta_sub"].configure(text=f"다음 {a2_txt}", text_color="#8c8c8c")
            elif is_current:
                refs["eta"].configure(text=a1_txt, text_color=self._route_color)
                refs["eta_sub"].configure(text=f"다음 {a2_txt}", text_color="#7a7a7a")
            else:
                refs["eta"].configure(text=a1_txt, text_color="#4a4a4a")
                refs["eta_sub"].configure(text=f"다음 {a2_txt}", text_color="#8c8c8c")

        self._last_updated = datetime.now()
        self._countdown = self.parent.REFRESH_SEC
        self._update_refresh_status()
        if self._alive:
            self.after(1000, self._tick)

        target_idx = focus_idx if focus_idx is not None else current_idx
        self.after(100, lambda: self._scroll_to_current(target_idx))

    def _arrival_text(self, info, minute_key, message_key):
        minute = info.get(minute_key)
        if minute is not None:
            stations = info.get(message_key)
            suffix = f" ({stations}정거장)" if stations is not None else ""
            return f"{minute}분 남음{suffix}"
        return "정보없음"

    def _update_refresh_status(self):
        if not self._last_updated:
            self.lbl_live.configure(text="갱신 준비 중...")
            return
        stamp = self._last_updated.strftime("%Y-%m-%d %H:%M:%S")
        self.lbl_live.configure(text=f"마지막 갱신: {stamp} · 갱신까지 {self._countdown}초")

    def _scroll_to_current(self, current_idx):
        if current_idx is None or not self._alive:
            return
        try:
            row = self._row_widgets[current_idx]["row"]
            self.update_idletasks()
            canvas = self.scroll._parent_canvas
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if not bbox:
                return
            total_h = bbox[3] - bbox[1]
            if total_h <= 0:
                return
            row_y = row.winfo_y()
            visible_h = canvas.winfo_height()
            target_y = max(0, row_y - visible_h // 2)
            canvas.yview_moveto(target_y / total_h)
        except Exception:
            pass

    def _line_state(self, idx, current_idx, total, is_focus, is_current):
        if current_idx is None:
            base = "#bfc4cb"
            node = "#8b8f97"
            return base if idx > 0 else "transparent", base if idx < total - 1 else "transparent", node

        if idx < current_idx:
            seg = "#a4aab3"
            node = "#8f949c"
        elif idx <= current_idx + 1:
            seg = self._route_color
            node = self._route_color
        else:
            seg = "#f0bc16"
            node = "#8f949c"

        if is_focus and not is_current:
            node = "#b35a00"

        top = seg if idx > 0 else "transparent"
        bottom = seg if idx < total - 1 else "transparent"
        return top, bottom, node

    def _find_arrival_info(self, arrivals, keys):
        for key in keys:
            if key in arrivals:
                return arrivals[key]
        return None

    def _is_focus_stop(self, stop):
        if not self.focus_station:
            return False

        focus_id = self.focus_station.get("id", "")
        focus_name = self.focus_station.get("name", "")
        focus_ars = self.focus_station.get("arsId", "")

        if focus_id and stop.get("id") == focus_id:
            return True
        if focus_ars and stop.get("arsId") == focus_ars:
            return True

        stop_name = stop.get("name", "")
        if focus_name and stop_name and (focus_name in stop_name or stop_name in focus_name):
            return True

        return False


# ─────────────────────────────────────────────────────────────
# 설정 화면
# ─────────────────────────────────────────────────────────────
class SettingsPage(ctk.CTkFrame):
    VOICES = {
        "여성 · 자연스러운 안내": "ko-KR-SunHiNeural",
        "남성 · 차분한 안내": "ko-KR-InJoonNeural",
    }
    CLOSE_BEHAVIOR = {
        "닫을 때 물어보기": None,
        "백그라운드로 전환": "background",
        "프로그램 종료": "exit",
    }

    RATES = {
        "느리게": "-10%",
        "보통": "-5%",
        "빠르게": "+0%",
    }

    def __init__(self, parent):
        super().__init__(parent, fg_color="#f5f7fa")
        self.parent = parent

        ctk.CTkLabel(
            self,
            text="설정",
            font=ctk.CTkFont(FONT, 22, "bold"),
            text_color="#003594",
        ).pack(anchor="w", padx=22, pady=(22, 4))

        ctk.CTkLabel(
            self,
            text="실행 중인 화면에 바로 적용됩니다.",
            font=ctk.CTkFont(FONT, 12),
            text_color="#777",
        ).pack(anchor="w", padx=22, pady=(0, 16))

        body = ctk.CTkFrame(self, fg_color="white", corner_radius=8, border_width=1, border_color="#e0e5ee")
        body.pack(fill="x", padx=22, pady=(0, 12))

        self.refresh_var = ctk.StringVar(value=f"{self.parent.REFRESH_SEC}초")
        self.voice_var = ctk.StringVar(value=self._current_voice_label())
        self.rate_var = ctk.StringVar(value=self._current_rate_label())
        self.close_behavior_var = ctk.StringVar(value=self._current_close_behavior_label())
        self.startup_var = ctk.BooleanVar(value=bool(self.parent.settings.get("run_on_startup")))

        self._row(
            body,
            "자동 갱신",
            values=["15초", "30초", "60초"],
            variable=self.refresh_var,
            width=150,
            command=self._apply_refresh,
        )

        self._row(
            body,
            "음성",
            values=list(self.VOICES.keys()),
            variable=self.voice_var,
            width=190,
            command=self._apply_voice,
        )

        self._row(
            body,
            "음성 속도",
            values=list(self.RATES.keys()),
            variable=self.rate_var,
            width=150,
            command=self._apply_rate,
        )

        self._row(
            body,
            "창 닫을 때 백그라운드 실행",
            values=list(self.CLOSE_BEHAVIOR.keys()),
            variable=self.close_behavior_var,
            width=180,
            command=self._apply_close_behavior,
        )

        self._switch_row(
            body,
            "Windows 시작 시 프로그램 실행",
            "로그인 후 자동으로 이 프로그램을 실행합니다.",
            self.startup_var,
            self._apply_startup_mode,
        )

        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.pack(fill="x", padx=22, pady=(2, 20))

        ctk.CTkButton(
            action_row,
            text="음성 테스트",
            width=120,
            height=34,
            fg_color="#003594",
            command=lambda: _speak_async("음성 안내 테스트입니다."),
        ).pack(side="left")

        ctk.CTkButton(
            action_row,
            text="← 돌아가기",
            width=110,
            height=34,
            fg_color="#555",
            command=self.parent._close_settings,
        ).pack(side="right")

        ctk.CTkLabel(
            self,
            text=f"버전  v{updater.CURRENT_VERSION}",
            font=ctk.CTkFont(FONT, 18),
            text_color="#aaa",
        ).pack(side="bottom", pady=(0, 16))

    def _row(self, parent, label, values, variable, width, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(14, 14))
        row.grid_columnconfigure(0, weight=1)
        row.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(FONT, 13, "bold"), text_color="#333").grid(row=0, column=0, sticky="w")
        widget = ctk.CTkOptionMenu(
            row,
            values=values,
            variable=variable,
            width=width,
            command=command,
        )
        widget.grid(row=0, column=1, sticky="e")

    def _switch_row(self, parent, label, description, variable, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(10, 10))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            row,
            text=label,
            font=ctk.CTkFont(FONT, 13, "bold"),
            text_color="#333",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            command=command,
            progress_color="#003594",
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            row,
            text=description,
            font=ctk.CTkFont(FONT, 11),
            text_color="#777",
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _current_voice_label(self):
        for label, voice in self.VOICES.items():
            if voice == TTS_VOICE:
                return label
        return "여성 · 자연스러운 안내"

    def _current_rate_label(self):
        for label, rate in self.RATES.items():
            if rate == TTS_RATE:
                return label
        return "보통"

    def _current_close_behavior_label(self):
        action = self.parent._close_action
        for label, value in self.CLOSE_BEHAVIOR.items():
            if value == action:
                return label
        return "닫을 때 물어보기"

    def _apply_refresh(self, value):
        seconds = int(value.replace("초", ""))
        self.parent.REFRESH_SEC = seconds
        self.parent._countdown = min(self.parent._countdown, seconds)
        self.parent.settings["refresh_sec"] = seconds
        self.parent._save_settings()
        self.parent.lbl_countdown.configure(text=f"갱신까지 {self.parent._countdown}초")
        self.parent._set_status(f"자동 갱신 주기 변경 · {seconds}초")

    def _apply_voice(self, value):
        global TTS_VOICE
        TTS_VOICE = self.VOICES.get(value, "ko-KR-SunHiNeural")
        self.parent.settings["tts_voice"] = TTS_VOICE
        self.parent._save_settings()
        self.parent._set_status("음성 안내 목소리 변경 완료")

    def _apply_rate(self, value):
        global TTS_RATE
        TTS_RATE = self.RATES.get(value, "-5%")
        self.parent.settings["tts_rate"] = TTS_RATE
        self.parent._save_settings()
        self.parent._set_status("음성 안내 속도 변경 완료")

    def _apply_close_behavior(self, value):
        action = self.CLOSE_BEHAVIOR.get(value)
        self.parent._close_action = action
        if action == "background":
            self.parent.settings["background_on_close"] = True
        elif action == "exit":
            self.parent.settings["background_on_close"] = False
        else:
            self.parent.settings["background_on_close"] = None
        self.parent._save_settings()
        status_map = {
            "background": "창 닫기 시 백그라운드 실행 사용",
            "exit": "창 닫기 시 프로그램 종료 사용",
            None: "창 닫을 때마다 동작을 다시 물어봄",
        }
        status = status_map.get(action, "창 닫기 동작 변경 완료")
        self.parent._set_status(status)

    def _apply_startup_mode(self):
        enabled = bool(self.startup_var.get())
        ok, error = _set_run_on_startup(enabled)
        if not ok:
            self.startup_var.set(not enabled)
            messagebox.showerror("시작 프로그램 설정 실패", error or "설정을 변경하지 못했습니다.")
            self.parent._set_status("Windows 시작 프로그램 설정 실패")
            return

        self.parent.settings["run_on_startup"] = enabled
        self.parent._save_settings()
        status = "Windows 시작 시 자동 실행 설정 완료" if enabled else "Windows 시작 자동 실행 해제 완료"
        self.parent._set_status(status)


# ─────────────────────────────────────────────────────────────
# 메인 앱
# ─────────────────────────────────────────────────────────────
class App(ctk.CTk):
    REFRESH_SEC = 30

    def __init__(self):
        super().__init__()
        self.title("부산광역시 실시간 버스 안내")
        self.geometry("1120x720")
        self.minsize(900, 600)
        _apply_window_icon(self)

        try:
            runtime_config = updater.get_runtime_config()
            self.api = BusanBusAPI(runtime_config)
        except ValueError as ex:
            messagebox.showerror("서버 연결 실패", str(ex))
            self.destroy()
            raise SystemExit(str(ex))
        self.settings = _load_settings()
        self._apply_saved_settings()
        self._tab = "route"
        self._countdown = self.REFRESH_SEC
        self._is_search_mode = False
        self._is_reconnect_mode = False
        self._search_query = ""
        self._search_tab = "route"
        self._is_refreshing_search = False
        self._is_settings_mode = False
        self._reconnect_notice_label = None
        self._route_popup = None
        self._managed_windows = set()
        self._background_hidden_windows = []
        self._main_board_loading = False
        self._station_preview_semaphore = threading.Semaphore(3)
        self._station_preview_cache = {}
        self._station_preview_cache_lock = threading.Lock()
        self._close_action = "background" if self.settings.get("background_on_close") is True else None
        if self.settings.get("background_on_close") is False:
            self._close_action = "exit"
        self._close_dialog = None
        self._is_in_background = False
        self._tray_icon = None
        self.protocol("WM_DELETE_WINDOW", self._handle_main_close)

        self._build_ui()
        self._load_main_board()
        self._tick()
        self.after(700, self._show_whats_new_if_needed)
        self.after(1500, self._check_integrity_bg)
        self.after(2000, self._check_for_update_bg)

    def _apply_saved_settings(self):
        global TTS_VOICE, TTS_RATE

        try:
            refresh_sec = int(self.settings.get("refresh_sec", 30))
        except (TypeError, ValueError):
            refresh_sec = 30
        self.REFRESH_SEC = max(15, refresh_sec)
        TTS_VOICE = str(self.settings.get("tts_voice") or TTS_VOICE)
        TTS_RATE = str(self.settings.get("tts_rate") or TTS_RATE)

    def _save_settings(self):
        _save_settings(self.settings)

    def _show_whats_new_if_needed(self):
        if not self.settings.get("show_whats_new", True):
            return
        self._show_whats_new_dialog()

    def _show_whats_new_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("업데이트 내용")
        _apply_window_icon(dialog)
        dialog.geometry("450x310")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.after(50, lambda: (dialog.lift(), dialog.focus_force(), dialog.grab_set()))

        ctk.CTkLabel(
            dialog,
            text="업데이트 내용",
            font=ctk.CTkFont(FONT, 18, "bold"),
            text_color="#003594",
        ).pack(anchor="w", padx=22, pady=(22, 8))

        ctk.CTkLabel(
            dialog,
            text="이번 버전에서 추가된 기능입니다.",
            font=ctk.CTkFont(FONT, 12),
            text_color="#666",
        ).pack(anchor="w", padx=22, pady=(0, 14))

        body = ctk.CTkFrame(dialog, fg_color="#f5f7fa", corner_radius=8)
        body.pack(fill="x", padx=22, pady=(0, 14))

        for line in [
            "- 최적화 및 보안 관련 안정화 코드 적용"
        ]:
            ctk.CTkLabel(
                body,
                text=line,
                font=ctk.CTkFont(FONT, 13),
                text_color="#333",
                justify="left",
            ).pack(anchor="w", padx=16, pady=(12 if line.startswith("- 백그라운드") else 0, 0))

        dont_show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            dialog,
            text="다시 표시 안 함",
            variable=dont_show_var,
            checkbox_width=18,
            checkbox_height=18,
        ).pack(anchor="w", padx=22, pady=(0, 18))

        def _close_dialog():
            self.settings["show_whats_new"] = not bool(dont_show_var.get())
            self._save_settings()
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

        ctk.CTkButton(
            dialog,
            text="확인",
            width=110,
            height=34,
            fg_color="#003594",
            command=_close_dialog,
        ).pack(pady=(0, 10))

    # ── UI 구성 ────────────────────────────────────────────────
    def _build_ui(self):
        # ── 헤더
        self.header = ctk.CTkFrame(self, height=54, fg_color="#003594", corner_radius=0)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)

        self.header_icon = None
        if _ICON_PATH:
            try:
                from PIL import Image
                self.header_icon = ctk.CTkImage(Image.open(_ICON_PATH), size=(32, 32))
            except Exception:
                self.header_icon = None

        ctk.CTkLabel(self.header, text="  부산광역시 실시간 버스 안내",
                     image=self.header_icon,
                     compound="left",
                     font=ctk.CTkFont(FONT, 19, "bold"),
                     text_color="white").pack(side="left", padx=18)

        self.lbl_time = ctk.CTkLabel(self.header, text="",
                                     font=ctk.CTkFont(FONT, 14),
                                     text_color="white")
        self.lbl_time.pack(side="right", padx=18)

        ctk.CTkButton(
            self.header,
            text="⚙",
            width=36,
            height=30,
            font=ctk.CTkFont(FONT, 16),
            fg_color="#002070",
            hover_color="#001858",
            text_color="white",
            command=self._open_settings,
        ).pack(side="right", padx=(0, 8))

        self.lbl_api_status = ctk.CTkLabel(self.header, text="  ● 서버 정상  ",
                                           font=ctk.CTkFont(FONT, 12),
                                           text_color="#2ecc71",
                                           fg_color="#002070",
                                           corner_radius=6)
        self.lbl_api_status.pack(side="right", padx=(0, 6))

        # ── 검색바
        self.search_bg = ctk.CTkFrame(self, height=54, fg_color="#eef2f8", corner_radius=0)
        self.search_bg.pack(fill="x")
        self.search_bg.pack_propagate(False)

        row = ctk.CTkFrame(self.search_bg, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=10)

        self.btn_route   = ctk.CTkButton(row, text="노선 검색",   width=88, height=32,
                                          fg_color="#003594",
                                          command=lambda: self._set_tab("route"))
        self.btn_station = ctk.CTkButton(row, text="정류소 검색", width=96, height=32,
                                          fg_color="#888",
                                          command=lambda: self._set_tab("station"))
        self.btn_route.pack(side="left", padx=(0, 4))
        self.btn_station.pack(side="left", padx=(0, 14))

        self.entry = ctk.CTkEntry(row, height=32, width=300,
                                  placeholder_text="버스 번호를 입력하세요 (예: 171, 1001)")
        self.entry.pack(side="left")
        # after(50) : Windows 한국어 IME가 Enter 키로 확정한 뒤
        # 엔트리 값을 읽어야 하므로 50ms 지연
        self.entry.bind("<Return>", lambda _: self.after(50, self._search))

        ctk.CTkButton(row, text="검색", width=58, height=32,
                      fg_color="#003594",
                      command=self._search).pack(side="left", padx=(6, 0))

        self.btn_back = ctk.CTkButton(row, text="← 전체 목록", width=90, height=32,
                                      fg_color="#555",
                                      command=self._back_to_main)
        self.btn_back.pack(side="left", padx=(8, 0))
        self.btn_back.pack_forget()   # 초기엔 숨김

        # ── 보드 헤더
        self.board_hdr = ctk.CTkFrame(self, height=40, fg_color="#dce6f5", corner_radius=0)
        self.board_hdr.pack(fill="x")
        self.board_hdr.pack_propagate(False)

        bh = ctk.CTkFrame(self.board_hdr, fg_color="transparent")
        bh.pack(fill="x", padx=18)

        self.lbl_board_title = ctk.CTkLabel(bh, text="부산광역시 주요 노선 현황",
                                            font=ctk.CTkFont(FONT, 14, "bold"),
                                            text_color="#003594")
        self.lbl_board_title.pack(side="left", pady=8)

        ctk.CTkButton(bh, text="↻  새로고침", width=88, height=26,
                      fg_color="#003594",
                      command=self._manual_refresh).pack(side="right", pady=7)

        self.lbl_countdown = ctk.CTkLabel(bh, text="",
                                          font=ctk.CTkFont(FONT, 12), text_color="#888")
        self.lbl_countdown.pack(side="right", padx=(0, 10), pady=8)

        # ── 스크롤 영역
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="#f0f4fa")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        # 카드를 담을 컨테이너 (3열 그리드)
        self.grid_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.grid_frame.pack(fill="x", padx=2)
        for c in range(3):
            self.grid_frame.columnconfigure(c, weight=1, uniform="col")

        self.settings_page = SettingsPage(self)

        # ── 상태바
        self.status_bar = ctk.CTkFrame(self, height=26, fg_color="#dce3ee", corner_radius=0)
        self.status_bar.pack(fill="x")
        self.status_bar.pack_propagate(False)

        self.lbl_status = ctk.CTkLabel(self.status_bar, text="",
                                       font=ctk.CTkFont(FONT, 11), text_color="#666")
        self.lbl_status.pack(side="left", padx=14)

        ctk.CTkLabel(
            self.status_bar,
            text="© 2026. Jiugae. All rights reserved.",
            font=ctk.CTkFont(FONT, 11),
            text_color="#777",
        ).pack(side="right", padx=14)

    def _register_managed_window(self, window):
        self._managed_windows.add(window)

    def _unregister_managed_window(self, window):
        self._managed_windows.discard(window)
        self._background_hidden_windows = [
            hidden for hidden in self._background_hidden_windows if hidden is not window
        ]

    def _handle_main_close(self):
        if self._close_action == "background":
            self._hide_to_background()
            return
        if self._close_action == "exit":
            self._exit_application()
            return
        self._show_close_choice_dialog()

    def _show_close_choice_dialog(self):
        if self._close_dialog and self._close_dialog.winfo_exists():
            self._close_dialog.lift()
            self._close_dialog.focus_force()
            return

        dialog = ctk.CTkToplevel(self)
        self._close_dialog = dialog
        dialog.title("닫기 동작 선택")
        _apply_window_icon(dialog)
        dialog.geometry("420x230")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.after(50, lambda: (dialog.lift(), dialog.focus_force(), dialog.grab_set()))
        dialog.protocol("WM_DELETE_WINDOW", self._dismiss_close_choice_dialog)

        ctk.CTkLabel(
            dialog,
            text="창을 닫을 때 동작을 선택해주세요.",
            font=ctk.CTkFont(FONT, 17, "bold"),
            text_color="#003594",
        ).pack(anchor="w", padx=22, pady=(24, 8))

        ctk.CTkLabel(
            dialog,
            text="이번 실행에서는 처음 한 번만 물어보고, 이후에는 같은 방식으로 동작합니다.",
            font=ctk.CTkFont(FONT, 12),
            text_color="#666",
            wraplength=370,
            justify="left",
        ).pack(anchor="w", padx=22)

        button_wrap = ctk.CTkFrame(dialog, fg_color="transparent")
        button_wrap.pack(fill="x", padx=22, pady=(24, 14))

        ctk.CTkButton(
            button_wrap,
            text="백그라운드로 전환",
            height=40,
            fg_color="#003594",
            command=lambda: self._set_close_action("background"),
        ).pack(fill="x")

        ctk.CTkButton(
            button_wrap,
            text="프로그램 종료",
            height=40,
            fg_color="#666",
            hover_color="#555",
            command=lambda: self._set_close_action("exit"),
        ).pack(fill="x", pady=(10, 0))

    def _dismiss_close_choice_dialog(self):
        if self._close_dialog and self._close_dialog.winfo_exists():
            try:
                self._close_dialog.grab_release()
            except Exception:
                pass
            self._close_dialog.destroy()
        self._close_dialog = None

    def _set_close_action(self, action):
        self._close_action = action
        self.settings["background_on_close"] = action == "background"
        self._save_settings()
        self._dismiss_close_choice_dialog()
        if action == "background":
            self._hide_to_background()
        else:
            self._exit_application()

    def _hide_to_background(self):
        if self._is_in_background:
            return

        visible_windows = []
        for window in list(self._managed_windows):
            if not window.winfo_exists():
                self._managed_windows.discard(window)
                continue
            try:
                if window.state() != "withdrawn":
                    visible_windows.append(window)
                    window.withdraw()
            except Exception:
                pass

        self._background_hidden_windows = visible_windows
        self.withdraw()

        if self._tray_icon is None:
            self._tray_icon = WindowsTrayIcon(
                on_open=lambda: self.after(0, self._restore_from_background),
                on_settings=lambda: self.after(0, lambda: self._restore_from_background(open_settings=True)),
                on_exit=lambda: self.after(0, self._exit_application),
                tooltip="부산광역시 실시간 버스 안내",
            )
        if not self._tray_icon.start():
            self.deiconify()
            for window in visible_windows:
                try:
                    if window.winfo_exists():
                        window.deiconify()
                except Exception:
                    pass
            self._background_hidden_windows = []
            messagebox.showerror("트레이 시작 실패", "트레이 아이콘을 시작하지 못했습니다.")
            return
        self._is_in_background = True
        updater.show_toast(
            "실시간 버스 안내",
            "백그라운드에서 계속 동작합니다. 트레이 메뉴에서 열기, 설정, 종료를 사용할 수 있습니다.",
        )

    def _restore_from_background(self, open_settings=False):
        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None

        self._is_in_background = False
        self.deiconify()

        for window in list(self._background_hidden_windows):
            try:
                if window.winfo_exists():
                    window.deiconify()
            except Exception:
                pass
        self._background_hidden_windows = []

        if open_settings:
            self._open_settings()

        self.after(50, lambda: (self.lift(), self.focus_force()))

    def _exit_application(self):
        if self._close_dialog and self._close_dialog.winfo_exists():
            self._dismiss_close_choice_dialog()

        for window in list(self._managed_windows):
            try:
                if hasattr(window, "_alive"):
                    window._alive = False
                if window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
        self._managed_windows.clear()
        self._background_hidden_windows = []

        if self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None

        try:
            self.withdraw()
        except Exception:
            pass
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _open_settings(self):
        if self._is_in_background:
            self._restore_from_background()
        if self._is_settings_mode:
            return
        self._is_settings_mode = True
        self.search_bg.pack_forget()
        self.board_hdr.pack_forget()
        self.scroll.pack_forget()
        self.settings_page.pack(fill="both", expand=True, before=self.status_bar)
        self._set_status("설정 화면")

    def _close_settings(self):
        if not self._is_settings_mode:
            return
        self._is_settings_mode = False
        self.settings_page.pack_forget()
        self.search_bg.pack(fill="x", after=self.header)
        self.board_hdr.pack(fill="x", after=self.search_bg)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(6, 4), after=self.board_hdr)
        self._set_status("설정 화면을 닫았습니다.")

    # ── 탭 전환 ────────────────────────────────────────────────
    def _set_tab(self, tab):
        self._tab = tab
        self._is_search_mode = False
        self._is_refreshing_search = False
        if tab == "route":
            self.btn_route.configure(fg_color="#003594")
            self.btn_station.configure(fg_color="#888")
            self.entry.configure(placeholder_text="버스 번호를 입력하세요 (예: 171, 1001)")
        else:
            self.btn_route.configure(fg_color="#888")
            self.btn_station.configure(fg_color="#003594")
            self.entry.configure(placeholder_text="정류소 이름을 입력하세요 (예: 부산역, 서면)")

        self.entry.delete(0, "end")
        self.after(0, self.entry.focus_set)

    # ── 검색 ───────────────────────────────────────────────────
    def _search(self):
        q = self.entry.get().strip()
        if not q:
            self._set_status("검색어를 입력해주세요.")
            return
        tab = self._tab
        # 즉시 검색 모드로 전환 → 자동 갱신이 결과를 덮어쓰는 race 방지
        self._is_search_mode = True
        self._search_query = q
        self._search_tab = tab
        self._is_refreshing_search = False
        self._countdown = self.REFRESH_SEC
        self.lbl_countdown.configure(text=f"갱신까지 {self._countdown}초")
        self._set_loading(f"'{q}' 검색 중...")
        threading.Thread(target=self._do_search, args=(q, tab), daemon=True).start()

    def _do_search(self, q, tab):
        is_refresh = self._is_refreshing_search
        try:
            if tab == "route":
                results = self.api.search_routes(q)
                self.after(1, lambda: self._show_route_results(results, q, refresh=is_refresh))
            else:
                results = self.api.search_stations(q)
                self.after(1, lambda: self._show_station_results(results, q, refresh=is_refresh))
        except Exception as ex:
            error = str(ex)
            self._is_refreshing_search = False
            self.after(1, lambda: self._show_api_status(False))
            self.after(1, lambda: self._show_search_error(error))

    def _show_route_results(self, results, query, refresh=False):
        self._enter_search_mode(f"노선 검색: '{query}'  ({len(results)}건)")
        self._clear_cards()

        if not results:
            self._show_notice(
                "검색 결과가 없습니다.",
                "버스 번호를 다시 확인하거나 다른 노선명으로 검색해 보세요.",
                tone="empty",
            )
            self._show_api_status(True)
            self._finish_search_refresh(refresh)
            self._set_status(f"검색 완료 · {datetime.now().strftime('%H:%M:%S')}")
            return

        for i, bus in enumerate(results):
            BusCard(self.grid_frame, bus, on_number_click=lambda b=bus: self._show_route_stops(b)).grid(
                row=i // 3, column=i % 3, padx=6, pady=6, sticky="ew")

        self._show_api_status(True)
        self._finish_search_refresh(refresh)
        self._set_status(f"검색 완료 · {datetime.now().strftime('%H:%M:%S')}")

    def _show_station_results(self, stations, query, refresh=False):
        self._enter_search_mode(f"정류소 검색: '{query}'  ({len(stations)}건)")
        self._clear_cards()

        if not stations:
            self._show_notice(
                "검색 결과가 없습니다.",
                "정류소 이름을 조금 다르게 입력해 보세요.",
                tone="empty",
            )
            self._show_api_status(True)
            self._finish_search_refresh(refresh)
            self._set_status(f"검색 완료 · {datetime.now().strftime('%H:%M:%S')}")
            return

        for i, st in enumerate(stations):
            self._station_card(st, i)

        self._show_api_status(True)
        self._finish_search_refresh(refresh)
        self._set_status(f"검색 완료 · {datetime.now().strftime('%H:%M:%S')}")

    def _finish_search_refresh(self, refresh=False):
        self._is_refreshing_search = False
        self._countdown = self.REFRESH_SEC
        self.lbl_countdown.configure(text=f"갱신까지 {self._countdown}초")

    def _station_card(self, st, idx):
        card = ctk.CTkFrame(self.grid_frame, corner_radius=8,
                            fg_color="white", border_width=1, border_color="#e0e0e0")
        card.grid(row=idx // 3, column=idx % 3, padx=6, pady=6, sticky="ew")

        bar = ctk.CTkFrame(card, width=7, fg_color="#003594", corner_radius=0)
        bar.pack(side="left", fill="y")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(side="left", fill="both", expand=True, padx=12, pady=10)

        ctk.CTkLabel(body, text=f"🚏  {st['name']}",
                     font=ctk.CTkFont(FONT, 15, "bold"),
                     text_color="#003594").pack(anchor="w")

        direction_label = ctk.CTkLabel(body, text="방면 확인 중...",
                                       font=ctk.CTkFont(FONT, 12),
                                       text_color="#777")
        direction_label.pack(anchor="w", pady=(1, 0))

        meta_text = self._station_meta_text(st)
        if meta_text:
            ctk.CTkLabel(body, text=meta_text,
                         font=ctk.CTkFont(FONT, 11),
                         text_color="#aaa").pack(anchor="w", pady=(1, 0))

        preview = ctk.CTkFrame(body, fg_color="transparent")
        preview.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(preview, text="도착 정보 확인 중...",
                     font=ctk.CTkFont(FONT, 11), text_color="#999").pack(anchor="w")

        if idx < 8:
            threading.Thread(target=self._load_station_preview, args=(st, preview, direction_label), daemon=True).start()
        else:
            direction_label.configure(text="방면 정보는 도착 정보에서 확인")
            self._render_station_preview(preview, None)

        ctk.CTkButton(body, text="도착 정보 보기 →", height=28, width=130,
                      fg_color="#003594",
                      command=lambda s=st: self._load_arrivals(s)).pack(anchor="w", pady=(8, 0))

    def _station_meta_text(self, station):
        parts = []
        stop_type = self._station_type_label(station.get("direction"))
        if stop_type:
            parts.append(stop_type)
        if station.get("arsId"):
            parts.append(f"정류소 {station['arsId']}")
        return " · ".join(parts)

    def _station_type_label(self, value):
        text = str(value or "").strip()
        labels = {
            "일반": "시내",
            "간선": "간선",
            "마을": "마을",
            "관광": "관광",
        }
        return labels.get(text, text)

    def _station_preview_key(self, station):
        return str(station.get("arsId") or station.get("id") or station.get("name") or "")

    def _get_cached_station_preview(self, station):
        key = self._station_preview_key(station)
        if not key:
            return None
        with self._station_preview_cache_lock:
            item = self._station_preview_cache.get(key)
            if not item:
                return None
            cached_at, value = item
            if time.time() - cached_at > 20:
                self._station_preview_cache.pop(key, None)
                return None
            return value

    def _set_cached_station_preview(self, station, direction, arrivals):
        key = self._station_preview_key(station)
        if not key:
            return
        with self._station_preview_cache_lock:
            self._station_preview_cache[key] = (
                time.time(),
                {
                    "direction": direction,
                    "arrivals": [dict(item) for item in arrivals] if arrivals is not None else None,
                },
            )

    def _load_station_preview(self, station, preview, direction_label):
        cached = self._get_cached_station_preview(station)
        if cached is not None:
            self.after(0, lambda: direction_label.configure(text=cached["direction"]))
            self.after(0, lambda: self._render_station_preview(preview, cached["arrivals"]))
            return

        acquired = self._station_preview_semaphore.acquire(timeout=0.1)
        if not acquired:
            self.after(0, lambda: direction_label.configure(text="방면 정보는 도착 정보에서 확인"))
            self.after(0, lambda: self._render_station_preview(preview, None))
            return
        try:
            arrivals = self.api.get_arrivals(station)
            direction = self.api.get_station_direction_summary(station, arrivals)
            preview_arrivals = arrivals[:2]
            self._set_cached_station_preview(station, direction, preview_arrivals)
            self.after(0, lambda: direction_label.configure(text=direction))
            self.after(0, lambda: self._render_station_preview(preview, preview_arrivals))
        except Exception:
            self.after(0, lambda: direction_label.configure(text="방면 정보 없음"))
            self.after(0, lambda: self._render_station_preview(preview, None))
        finally:
            self._station_preview_semaphore.release()

    def _render_station_preview(self, preview, arrivals):
        try:
            for w in preview.winfo_children():
                w.destroy()

            if arrivals is None:
                ctk.CTkLabel(preview, text="도착 정보 보기에서 확인하세요.",
                             font=ctk.CTkFont(FONT, 11), text_color="#999").pack(anchor="w")
                return

            if not arrivals:
                ctk.CTkLabel(preview, text="현재 표시할 도착 정보가 없습니다.",
                             font=ctk.CTkFont(FONT, 11), text_color="#999").pack(anchor="w")
                return

            for bus in arrivals[:2]:
                color = BUS_COLORS.get(bus.get("type", "normal"), "#3d74db")
                arr = bus.get("arrival1")
                eta = "곧 도착" if arr is not None and arr <= 1 else (f"{arr}분 후" if arr is not None else "정보없음")
                ctk.CTkLabel(
                    preview,
                    text=f"{bus.get('number', '')}번  ·  {eta}",
                    font=ctk.CTkFont(FONT, 12, "bold"),
                    text_color=color,
                ).pack(anchor="w")
        except Exception:
            pass

    def _load_arrivals(self, station):
        self._set_status(f"{station['name']} 도착 정보 불러오는 중...")
        threading.Thread(target=self._do_arrivals, args=(station,), daemon=True).start()

    def _do_arrivals(self, station):
        try:
            arrivals = self.api.get_arrivals(station)
            self.after(0, lambda: ArrivalWindow(self, station, arrivals))
            self.after(0, lambda: self._show_api_status(True))
            self.after(0, lambda: self._set_status(f"도착 정보 로드 완료 · {datetime.now().strftime('%H:%M:%S')}"))
        except Exception as ex:
            error = str(ex)
            self.after(0, lambda: self._show_api_status(False))
            self.after(0, lambda: self._set_status(self._format_error_status("도착 정보 불러오기 실패", error)))

    # ── 메인 보드 ──────────────────────────────────────────────
    def _load_main_board(self):
        if self._main_board_loading:
            return
        self._main_board_loading = True
        self._is_search_mode = False
        self._is_refreshing_search = False
        self._is_reconnect_mode = False
        self.lbl_board_title.configure(text="부산광역시 주요 노선 현황")
        self.btn_back.pack_forget()
        self._set_loading("불러오는 중...")
        threading.Thread(target=self._do_main_board, daemon=True).start()

    def _do_main_board(self):
        try:
            data = self.api.get_main_board()
            self.after(0, lambda: self._render_main_board(data))
        except Exception as ex:
            error = str(ex)
            self.after(0, lambda: self._show_api_error(error))

    def _render_main_board(self, data):
        self._main_board_loading = False
        self._clear_cards()
        for i, bus in enumerate(data):
            BusCard(self.grid_frame, bus, on_number_click=lambda b=bus: self._show_route_stops(b)).grid(
                row=i // 3, column=i % 3, padx=6, pady=6, sticky="ew")
        self._countdown = self.REFRESH_SEC
        self._is_reconnect_mode = False
        self.lbl_countdown.configure(text=f"갱신까지 {self._countdown}초")
        self._show_api_status(True)
        self._set_status(
            f"마지막 갱신: {datetime.now().strftime('%H:%M:%S')}  ·  총 {len(data)}개 노선"
        )

    def _manual_refresh(self):
        if self._is_search_mode:
            if self._search_query and not self._is_refreshing_search:
                self._is_refreshing_search = True
                self.lbl_countdown.configure(text="검색 갱신 중...")
                threading.Thread(
                    target=self._do_search,
                    args=(self._search_query, self._search_tab),
                    daemon=True,
                ).start()
            return
        self._countdown = self.REFRESH_SEC
        self._is_reconnect_mode = False
        self._load_main_board()

    def _back_to_main(self):
        self.entry.delete(0, "end")
        self._search_query = ""
        self._is_refreshing_search = False
        self._load_main_board()
        self.after(50, self.entry.focus_set)

    def _enter_search_mode(self, title):
        self._is_search_mode = True
        self.lbl_board_title.configure(text=title)
        self.btn_back.pack(side="left", padx=(8, 0))
        self.after(50, self.entry.focus_set)

    def _show_route_stops(self, route, focus_station=None):
        self._set_status(f"{route['number']}번 경유 정류장 불러오는 중...")
        threading.Thread(target=self._do_route_stops, args=(route, focus_station), daemon=True).start()

    def _do_route_stops(self, route, focus_station):
        try:
            stops = self.api.get_route_stations(route["id"])
            self.after(0, lambda: self._open_route_popup(route, stops, focus_station))
            self.after(0, lambda: self._show_api_status(True))
            self.after(0, lambda: self._set_status(f"경유 정류장 로드 완료 · {datetime.now().strftime('%H:%M:%S')}"))
        except Exception as ex:
            error = str(ex)
            self.after(0, lambda: self._show_api_status(False))
            self.after(0, lambda: self._set_status(self._format_error_status("경유 정류장 불러오기 실패", error)))

    def _open_route_popup(self, route, stops, focus_station):
        if self._route_popup is not None and self._route_popup._alive:
            current_route_id = self._route_popup.route.get("id")
            if current_route_id != route.get("id"):
                self._route_popup._alive = False
                self._route_popup.destroy()
            else:
                self._route_popup.focus_force()
                self._route_popup.lift()
                return
        popup = RouteStopsWindow(self, route, stops, focus_station=focus_station)
        popup.lift()
        popup.focus_force()
        self._route_popup = popup

    # ── 공통 유틸 ──────────────────────────────────────────────
    def _clear_cards(self):
        self._reconnect_notice_label = None
        for w in self.grid_frame.winfo_children():
            w.destroy()

    def _set_loading(self, msg):
        self._set_status(msg)

    def _set_status(self, msg):
        self.lbl_status.configure(text=msg)

    def _show_api_status(self, is_ok):
        if is_ok:
            self.lbl_api_status.configure(text="  ● 서버 정상  ", text_color="#2ecc71")
        else:
            self.lbl_api_status.configure(text="  ● 서버 오류  ", text_color="#ff4d4f")

    # ── 무결성 검사 ───────────────────────────────────────────
    def _check_integrity_bg(self):
        threading.Thread(target=self._do_check_integrity, daemon=True).start()

    def _do_check_integrity(self):
        ok, _ = updater.check_integrity()
        if not ok:
            self.after(0, self._show_integrity_warning)

    def _show_integrity_warning(self):
        import sys

        # 메인 창 즉시 비활성화
        self.attributes("-disabled", True)
        self._tick = lambda: None  # 타이머 무력화

        dialog = ctk.CTkToplevel(self)
        dialog.title("무결성 검사 실패")
        _apply_window_icon(dialog)
        dialog.geometry("420x200")
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", lambda: sys.exit(1))
        dialog.after(50, lambda: (dialog.lift(), dialog.focus_force(), dialog.grab_set()))

        ctk.CTkLabel(dialog, text="⚠  실행 파일 변조 감지",
                     font=ctk.CTkFont(FONT, 16, "bold"),
                     text_color="#b00020").pack(pady=(28, 8))

        ctk.CTkLabel(dialog,
                     text="이 프로그램이 공식 배포본과 다릅니다.\n공식 배포 경로에서 다시 설치해 주세요.",
                     font=ctk.CTkFont(FONT, 12), text_color="#555",
                     wraplength=360, justify="center").pack(pady=(0, 24))

        ctk.CTkButton(dialog, text="확인 후 종료", width=130,
                      fg_color="#b00020",
                      command=lambda: sys.exit(1)).pack()

    # ── 업데이트 확인 ──────────────────────────────────────────
    def _check_for_update_bg(self):
        threading.Thread(target=self._do_check_for_update, daemon=True).start()

    def _do_check_for_update(self):
        info = updater.check_for_update()
        if info:
            self.after(0, lambda: self._show_update_dialog(info))

    def _show_update_dialog(self, info):
        dialog = ctk.CTkToplevel(self)
        dialog.title("업데이트 알림")
        _apply_window_icon(dialog)
        dialog.geometry("420x240")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.after(50, lambda: (dialog.lift(), dialog.focus_force()))

        ctk.CTkLabel(dialog, text=f"새 버전  {info['version']}  이 출시되었습니다.",
                     font=ctk.CTkFont(FONT, 16, "bold"),
                     text_color="#003594").pack(pady=(28, 6))

        notes = (info.get("notes") or "").strip()
        if notes:
            ctk.CTkLabel(dialog, text=notes[:180],
                         font=ctk.CTkFont(FONT, 12), text_color="#555",
                         wraplength=360, justify="center").pack(pady=(0, 14))
        else:
            ctk.CTkLabel(dialog, text="지금 업데이트하시겠습니까?",
                         font=ctk.CTkFont(FONT, 12), text_color="#555").pack(pady=(0, 14))

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack()

        def _on_update_done(ok):
            if ok:
                self.after(0, lambda: self._set_status("설치 시작 — 잠시 후 앱이 종료됩니다."))
                self.after(1500, self._exit_application)
            else:
                self.after(0, lambda: self._show_api_status(False))
                self.after(0, lambda: self._set_status("업데이트 다운로드 실패 — 네트워크를 확인해 주세요."))
                updater.show_toast("업데이트 실패", "설치파일 다운로드 중 오류가 발생했습니다.")

        def _start_update():
            if not info.get("download_url"):
                messagebox.showinfo("업데이트", "다운로드 주소를 찾을 수 없습니다.\nGitHub Releases에서 직접 다운로드해 주세요.")
                dialog.destroy()
                return

            confirm = messagebox.askyesno(
                "업데이트 설치 확인",
                "업데이트를 설치하면 현재 열려 있는 창이 닫히고 앱이 종료됩니다.\n계속하시겠습니까?",
                parent=dialog,
            )
            if not confirm:
                return

            dialog.destroy()
            self.lbl_api_status.configure(text="  ● 다운로드 중  ", text_color="#f39c12")
            self._set_status("업데이트 다운로드 중...")
            threading.Thread(
                target=updater.download_and_launch,
                args=(info["download_url"],),
                kwargs={
                    "on_progress": lambda p: self.after(0, lambda: self._set_status(f"다운로드 중... {int(p * 100)}%")),
                    "on_done": _on_update_done,
                },
                daemon=True,
            ).start()

        ctk.CTkButton(btn_row, text="업데이트 설치", width=130,
                      fg_color="#003594", command=_start_update).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="나중에", width=90,
                      fg_color="#888", command=dialog.destroy).pack(side="left")

    def _format_error_status(self, prefix, error):
        return f"API 호출 실패 | 에러코드({self._extract_error_code(error)})"

    def _extract_error_code(self, error):
        text = str(error or "")
        http_match = re.search(r"HTTP\s*(\d+)", text, re.IGNORECASE)
        if http_match:
            return http_match.group(1)

        code_match = re.search(r"(?:headerCd|코드|code)\D*([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        if code_match:
            return code_match.group(1)

        if "Timeout" in text:
            return "TIMEOUT"
        if "ConnectionError" in text:
            return "CONNECTION"
        if "JSON" in text or "XML" in text:
            return "PARSE"
        if "SERVICE KEY" in text or "BUSAN_BUS_API_KEY" in text or "서비스키" in text:
            return "AUTH"
        return "UNKNOWN"

    def _show_notice(self, title, body="", tone="info"):
        colors = {
            "info": ("#003594", "#5f6b7a"),
            "empty": ("#555", "#777"),
            "error": ("#b00020", "#6d4248"),
        }
        title_color, body_color = colors.get(tone, colors["info"])

        frame = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        frame.grid(row=0, column=0, columnspan=3, pady=54, padx=20, sticky="ew")

        ctk.CTkLabel(
            frame,
            text=title,
            font=ctk.CTkFont(FONT, 17, "bold"),
            text_color=title_color,
        ).pack(pady=(0, 6))

        body_label = None
        if body:
            body_label = ctk.CTkLabel(
                frame,
                text=body,
                font=ctk.CTkFont(FONT, 13),
                text_color=body_color,
                wraplength=520,
                justify="center",
            )
            body_label.pack()

        return body_label

    def _show_search_error(self, error):
        self._is_refreshing_search = False
        self._countdown = self.REFRESH_SEC
        self._clear_cards()
        self._show_notice(
            "검색 결과를 불러오지 못했습니다.",
            "잠시 후 다시 시도해 주세요. 같은 문제가 계속되면 API 키나 네트워크 상태를 확인해 주세요.",
            tone="error",
        )
        self._set_status(self._format_error_status("검색 실패", error))

    def _show_api_error(self, error):
        self._main_board_loading = False
        self._clear_cards()
        self._reconnect_notice_label = self._show_notice(
            "실시간 정보를 불러오지 못했습니다.",
            f"API 연결이 원활하지 않습니다. {self.REFRESH_SEC}초 후 자동으로 다시 연결을 시도합니다.",
            tone="error",
        )
        self._show_api_status(False)
        self._is_reconnect_mode = True
        self._countdown = self.REFRESH_SEC
        self.lbl_countdown.configure(text=f"재연결까지 {self._countdown}초")
        self._update_reconnect_notice()
        self._set_status(self._format_error_status("API 호출 실패", error))

    def _update_reconnect_notice(self):
        if not self._reconnect_notice_label or not self._is_reconnect_mode:
            return
        try:
            self._reconnect_notice_label.configure(
                text=f"API 연결이 원활하지 않습니다. {self._countdown}초 후 자동으로 다시 연결을 시도합니다."
            )
        except Exception:
            self._reconnect_notice_label = None

    # ── 1초 타이머 ────────────────────────────────────────────
    def _tick(self):
        self.lbl_time.configure(text=datetime.now().strftime("%Y-%m-%d  %H:%M:%S"))

        if self._is_settings_mode:
            self.after(1000, self._tick)
            return

        self._countdown -= 1
        if self._countdown <= 0:
            if self._is_search_mode:
                if self._search_query and not self._is_refreshing_search:
                    self._is_refreshing_search = True
                    self.lbl_countdown.configure(text="검색 갱신 중...")
                    threading.Thread(
                        target=self._do_search,
                        args=(self._search_query, self._search_tab),
                        daemon=True,
                    ).start()
            else:
                self.lbl_countdown.configure(text="재연결 중..." if self._is_reconnect_mode else "갱신 중...")
                if self._is_reconnect_mode and self._reconnect_notice_label:
                    self._reconnect_notice_label.configure(text="API 연결을 다시 시도하는 중입니다.")
                self._load_main_board()
        else:
            label = "갱신까지"
            if not self._is_search_mode and self._is_reconnect_mode:
                label = "재연결까지"
            self.lbl_countdown.configure(text=f"{label} {self._countdown}초")
            self._update_reconnect_notice()

        self.after(1000, self._tick)


if __name__ == "__main__":
    app = App()
    app.mainloop()
