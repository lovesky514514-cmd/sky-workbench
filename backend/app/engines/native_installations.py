"""读取厂商安装登记和 Windows 文件版本，不启动软件。"""

from __future__ import annotations

import os
from pathlib import Path
import re


def registered_install_roots(engine: str) -> list[Path]:
    if os.name != "nt":
        return []
    import winreg

    patterns = {"MATLAB": r"\bMATLAB\b", "Materials Studio": r"\bMaterials Studio\b", "LAMMPS": r"\bLAMMPS\b"}
    if engine not in patterns:
        return []
    paths = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0,
                                    winreg.KEY_READ | view) as uninstall:
                    for index in range(min(winreg.QueryInfoKey(uninstall)[0], 8192)):
                        try:
                            with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as entry:
                                name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                if not isinstance(name, str) or not re.search(patterns[engine], name, re.I):
                                    continue
                                location = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                if not isinstance(location, str) or location.startswith("\\\\"):
                                    continue
                                path = Path(location)
                                if path.is_absolute() and path.is_dir() and path not in paths:
                                    paths.append(path)
                        except OSError:
                            continue
            except OSError:
                continue
    return paths


def materials_server(executable: str) -> Path | None:
    launcher = Path(executable)
    # 厂商服务器启动器位于 <安装>/etc/Scripting/bin，兼容不同版本安装根。
    if len(launcher.parents) >= 4 and tuple(part.lower() for part in launcher.parent.parts[-3:]) == ("etc", "scripting", "bin"):
        server = launcher.parents[3] / "bin" / "MatServer.exe"
        return server if server.is_file() else None
    return None


def file_product_version(path: Path | None) -> str:
    if os.name != "nt" or path is None:
        return "UNVERIFIED"
    import ctypes
    from ctypes import wintypes

    class FixedInfo(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "signature", "struct_version", "file_ms", "file_ls", "product_ms", "product_ls",
            "flags_mask", "flags", "os", "type", "subtype", "date_ms", "date_ls")]

    try:
        version = ctypes.WinDLL("version.dll", winmode=0x800)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        version.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        unused = wintypes.DWORD()
        size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
        if not 0 < size <= 1_000_000:
            return "UNVERIFIED"
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return "UNVERIFIED"
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < ctypes.sizeof(FixedInfo):
            return "UNVERIFIED"
        info = ctypes.cast(pointer, ctypes.POINTER(FixedInfo)).contents
        if info.signature != 0xFEEF04BD:
            return "UNVERIFIED"
        return ".".join(str(value) for value in (info.product_ms >> 16, info.product_ms & 65535,
                                                 info.product_ls >> 16, info.product_ls & 65535))
    except (OSError, ValueError, AttributeError):
        return "UNVERIFIED"
