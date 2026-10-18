"""Lightweight Windows QR window rendered as square bitmap pixels."""

import ctypes
import os
import threading
from ctypes import wintypes

from .qrlogin import _png_to_grid

_WINDOW_WIDTH = 620
_WINDOW_HEIGHT = 700
_MODULE_PIXELS = 8


def rasterize_qr(png: bytes, module_pixels: int = _MODULE_PIXELS) -> tuple[int, int, bytes]:
    """Return a top-down BGRA bitmap with a four-module quiet zone."""
    if module_pixels < 1:
        raise ValueError("module_pixels must be positive")
    grid = _png_to_grid(png)
    if not grid or not grid[0] or any(len(row) != len(grid[0]) for row in grid):
        raise ValueError("QR module grid must be non-empty and rectangular")

    quiet = 4
    width_modules = len(grid[0]) + quiet * 2
    height_modules = len(grid) + quiet * 2
    white = b"\xff\xff\xff\xff" * module_pixels
    black = b"\x00\x00\x00\xff" * module_pixels
    output = bytearray()
    white_row = white * width_modules

    for _ in range(quiet * module_pixels):
        output.extend(white_row)
    for row in grid:
        pixels = white * quiet + b"".join(black if dark else white
                                             for dark in row) + white * quiet
        for _ in range(module_pixels):
            output.extend(pixels)
    for _ in range(quiet * module_pixels):
        output.extend(white_row)

    width = width_modules * module_pixels
    height = height_modules * module_pixels
    return width, height, bytes(output)


class _RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", _RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_ubyte * 32)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG), ("lPrivate", wintypes.DWORD)]


class _WNDCLASSW(ctypes.Structure):
    pass


_LRESULT = ctypes.c_ssize_t
_HCURSOR = wintypes.HANDLE
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
_WNDCLASSW._fields_ = [
    ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
    ("hCursor", _HCURSOR), ("hbrBackground", wintypes.HBRUSH),
    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte),
                ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


class NativeQrWindow:
    """A small native window; the login poll loop stays on the caller thread."""

    def __init__(self, png: bytes, status: str, hint: str):
        if os.name != "nt":
            raise OSError("native QR windows are only available on Windows")
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.closed = threading.Event()
        self._startup_error = None
        self._hwnd = None
        self._user32 = None
        self._kernel32 = None
        self._gdi32 = None
        self._status = status
        self._hint = hint
        self._set_bitmap(png)
        self._thread = threading.Thread(target=self._run, name="qr-window", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.close()
            raise OSError("timed out opening native QR window")
        if self._startup_error:
            self.close()
            raise OSError(f"could not open native QR window: {self._startup_error}")

    def _set_bitmap(self, png: bytes) -> None:
        width, height, pixels = rasterize_qr(png)
        self._bitmap_width = width
        self._bitmap_height = height
        self._bitmap = pixels

    def update(self, png: bytes | None, status: str, hint: str = "") -> None:
        if png is not None:
            bitmap = rasterize_qr(png)
        else:
            bitmap = None
        with self._lock:
            if bitmap is not None:
                self._bitmap_width, self._bitmap_height, self._bitmap = bitmap
            self._status = status
            self._hint = hint
            hwnd = self._hwnd
        if hwnd and not self.closed.is_set():
            self._user32.InvalidateRect(hwnd, None, True)

    def close(self) -> None:
        hwnd = self._hwnd
        if hwnd and not self.closed.is_set():
            self._user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=2)
        self.closed.set()

    def _run(self) -> None:
        try:
            self._open_window()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            self.closed.set()

    def _open_window(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._set_api_signatures()
        hinstance = self._kernel32.GetModuleHandleW(None)
        self._class_name = f"SuperstarQrWindow.{id(self):x}"
        self._wndproc = _WNDPROC(self._window_proc)

        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = hinstance
        window_class.hCursor = self._user32.LoadCursorW(None, ctypes.c_void_p(32512))
        window_class.hbrBackground = self._gdi32.GetStockObject(0)  # WHITE_BRUSH
        window_class.lpszClassName = self._class_name
        if not self._user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())

        width, height = _WINDOW_WIDTH, _WINDOW_HEIGHT
        x = (self._user32.GetSystemMetrics(0) - width) // 2
        y = (self._user32.GetSystemMetrics(1) - height) // 2
        hwnd = self._user32.CreateWindowExW(
            0, self._class_name, "超星扫码登录", 0x00CF0000 | 0x10000000,
            x, y, width, height, None, None, hinstance, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd
        self._user32.ShowWindow(hwnd, 5)  # SW_SHOW
        self._user32.UpdateWindow(hwnd)
        self._ready.set()

        message = _MSG()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result <= 0:
                break
            self._user32.TranslateMessage(ctypes.byref(message))
            self._user32.DispatchMessageW(ctypes.byref(message))
        self.closed.set()
        self._user32.UnregisterClassW(self._class_name, hinstance)

    def _set_api_signatures(self) -> None:
        user32, gdi32 = self._user32, self._gdi32
        kernel32 = self._kernel32
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
        user32.LoadCursorW.restype = _HCURSOR
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UpdateWindow.argtypes = [wintypes.HWND]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(_MSG), wintypes.HWND,
                                       wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = _LRESULT
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.InvalidateRect.argtypes = [wintypes.HWND,
                                          ctypes.POINTER(_RECT), wintypes.BOOL]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.GetStockObject.restype = wintypes.HGDIOBJ
        user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(_RECT), wintypes.HBRUSH]
        user32.FillRect.restype = ctypes.c_int
        gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SetDIBitsToDevice.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPCVOID,
            ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
        ]
        gdi32.SetDIBitsToDevice.restype = ctypes.c_int
        gdi32.StretchDIBits.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.LPCVOID, ctypes.POINTER(_BITMAPINFO), wintypes.UINT, wintypes.DWORD,
        ]
        gdi32.StretchDIBits.restype = ctypes.c_int
        user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                     ctypes.POINTER(_RECT), wintypes.UINT]

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == 0x000F:  # WM_PAINT
            self._paint(hwnd)
            return 0
        if message == 0x0014:  # WM_ERASEBKGND
            return 1
        if message == 0x0010:  # WM_CLOSE
            self._user32.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:  # WM_DESTROY
            self._hwnd = None
            self.closed.set()
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _paint(self, hwnd) -> None:
        ps = _PAINTSTRUCT()
        hdc = self._user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            client = _RECT()
            self._user32.GetClientRect(hwnd, ctypes.byref(client))
            self._user32.FillRect(hdc, ctypes.byref(client),
                                  self._gdi32.GetStockObject(0))  # WHITE_BRUSH
            self._gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
            self._gdi32.SetTextColor(hdc, 0x00141414)
            font = self._gdi32.GetStockObject(17)  # DEFAULT_GUI_FONT
            old_font = self._gdi32.SelectObject(hdc, font)

            status_rect = _RECT(18, 12, client.right - 18, 38)
            self._user32.DrawTextW(hdc, self._status, -1, ctypes.byref(status_rect),
                                   0x00000001 | 0x00000020)  # CENTER | VERTCENTER
            hint_rect = _RECT(18, 38, client.right - 18, 62)
            self._user32.DrawTextW(hdc, self._hint, -1, ctypes.byref(hint_rect),
                                   0x00000001 | 0x00000020 | 0x00000004)  # CENTER | VCENTER | SINGLELINE

            with self._lock:
                width, height, pixels = (self._bitmap_width, self._bitmap_height,
                                         self._bitmap)
            max_width = client.right - 48
            max_height = client.bottom - 90
            scale = min(max_width / width, max_height / height, 1.0)
            draw_width = max(1, int(width * scale))
            draw_height = max(1, int(height * scale))
            left = (client.right - draw_width) // 2
            top = 72 + max(0, (client.bottom - 72 - draw_height) // 2)
            header = _BITMAPINFOHEADER(
                ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0,
                len(pixels), 0, 0, 0, 0)
            bitmap_info = _BITMAPINFO(header, (_RGBQUAD * 1)())
            pixel_buffer = ctypes.create_string_buffer(pixels)
            self._gdi32.StretchDIBits(
                hdc, left, top, draw_width, draw_height, 0, 0, width, height,
                pixel_buffer, ctypes.byref(bitmap_info), 0, 0x00CC0020)  # SRCCOPY
            if old_font:
                self._gdi32.SelectObject(hdc, old_font)
        finally:
            self._user32.EndPaint(hwnd, ctypes.byref(ps))
