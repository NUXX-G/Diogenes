"""Ventanas de nivel superior de Windows: listarlas, pedirles que se cierren y, como último recurso, terminar su proceso.

Todo lo que toca la API de Windows está aquí detrás de `WindowBackend`, para que el resto (y los tests, también en Linux)
trabaje con un doble de prueba.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

WM_CLOSE = 0x0010
GW_OWNER = 4
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class Win:
    hwnd: int
    pid: int
    title: str
    exe: str  # ruta completa del ejecutable, o "" si no se pudo leer
    cls: str = ""  # clase de la ventana ("CabinetWClass" = una carpeta del Explorador)

    @property
    def exe_name(self) -> str:
        return PureWindowsPath(self.exe).name.lower() if self.exe else ""


class WindowBackend(Protocol):
    def list_windows(self) -> list[Win]: ...

    def post_close(self, hwnd: int) -> None:
        """Equivale a pulsar la X: la app decide (puede preguntar si guardar)."""

    def terminate(self, pid: int) -> bool:
        """Termina el proceso sin dejar guardar nada. Devuelve si se pudo."""


class WinBackend:
    """Implementación real con ctypes (user32 y kernel32). Solo se construye en Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Las ventanas de Windows solo existen en Windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._wt = ctypes, wintypes
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self._user32, self._kernel32
        self._proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [self._proc_type, wintypes.LPARAM]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

    def _exe_of(self, pid: int) -> str:
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = self._wt.DWORD(1024)
            buf = self._ctypes.create_unicode_buffer(size.value)
            ok = self._kernel32.QueryFullProcessImageNameW(handle, 0, buf, self._ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            self._kernel32.CloseHandle(handle)

    def list_windows(self) -> list[Win]:
        found: list[Win] = []
        u, ctypes = self._user32, self._ctypes

        def visit(hwnd: int, _lparam: int) -> bool:
            if not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, GW_OWNER):
                return True  # ocultas y ventanas dependientes de otra no son "la ventana" de una app
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            pid = self._wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, name, 256)
            found.append(Win(int(hwnd), pid.value, buf.value, self._exe_of(pid.value), name.value))
            return True

        u.EnumWindows(self._proc_type(visit), 0)
        return found

    def post_close(self, hwnd: int) -> None:
        self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def terminate(self, pid: int) -> bool:
        handle = self._kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(self._kernel32.TerminateProcess(handle, 1))
        finally:
            self._kernel32.CloseHandle(handle)
