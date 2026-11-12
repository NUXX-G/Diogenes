"""Lee a dónde apunta un acceso `.lnk` (formato MS-SHLLINK) sin COM, para poder probarlo también fuera de Windows.

Un `.lnk` guarda su destino en varios sitios y Windows no siempre usa el mismo, así que se devuelven todos: quien decide
mira cualquiera de ellos. Se leen en el momento de lanzar: el índice puede llevar minutos cacheado.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

HEADER_SIZE = 0x4C
LINK_CLSID = bytes.fromhex("0114020000000000C000000000000046")
MAX_BYTES = 1 << 20  # un .lnk mide unos pocos kB

HAS_ID_LIST = 0x0001
HAS_LINK_INFO = 0x0002
HAS_NAME = 0x0004
HAS_RELATIVE_PATH = 0x0008
HAS_WORKING_DIR = 0x0010
HAS_ARGUMENTS = 0x0020
HAS_ICON = 0x0040
IS_UNICODE = 0x0080
HAS_DARWIN_ID = 0x1000

BLOCK_ENVIRONMENT = 0xA0000001
BLOCK_DARWIN = 0xA0000006
FILE_ENTRY_MASK = 0x70
FILE_ENTRY_TYPE = 0x30


@dataclass(frozen=True)
class LinkTarget:
    paths: tuple[str, ...] = ()  # rutas de LinkInfo, variables de entorno y ruta relativa
    shell_name: str = ""  # nombre del último elemento de la lista de IDs; puede ser un nombre corto (FOO~1.EXE)
    arguments: str = ""
    advertised: bool = False  # acceso anunciado de Windows Installer: el destino lo resuelve MSI, no hay ruta


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _ansi(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("cp1252", errors="replace")


def _wide(data: bytes, offset: int) -> str:
    end = offset
    while data[end : end + 2] != b"\0\0":
        if end >= len(data):
            raise ValueError("cadena sin terminar")
        end += 2
    return data[offset:end].decode("utf-16-le", errors="replace")


def _link_info_path(info: bytes) -> str:
    header, flags = _u32(info, 4), _u32(info, 8)
    suffix = _ansi(info, _u32(info, 24)) if _u32(info, 24) else ""
    if flags & 1:
        if header >= 0x24 and _u32(info, 28):
            wide_suffix = _wide(info, _u32(info, 32)) if _u32(info, 32) else ""
            return _wide(info, _u32(info, 28)) + wide_suffix
        return _ansi(info, _u32(info, 16)) + suffix
    if flags & 2:
        network = _u32(info, 20)
        share = _ansi(info, network + _u32(info, network + 8))
        return f"{share}\\{suffix}" if suffix else share
    return ""


def _shell_name(id_list: bytes) -> str:
    """Nombre del último elemento de la lista de IDs, si es un archivo o carpeta."""
    offset, last = 0, b""
    while offset + 2 <= len(id_list):
        size = _u16(id_list, offset)
        if size < 2:
            break
        last = id_list[offset : offset + size]
        offset += size
    if len(last) <= 14 or last[2] & FILE_ENTRY_MASK != FILE_ENTRY_TYPE:
        return ""
    return _ansi(last, 14)


def _environment_path(block: bytes) -> str:
    """Bloque de variables de entorno: 260 bytes ANSI y después 520 UTF-16 (`%windir%\\system32\\cmd.exe`)."""
    return _wide(block, 8 + 260) or _ansi(block, 8)


def parse(data: bytes) -> LinkTarget | None:
    """El destino del acceso, o None si los bytes no son un `.lnk` que se pueda leer."""
    try:
        if len(data) < HEADER_SIZE or _u32(data, 0) != HEADER_SIZE or data[4:20] != LINK_CLSID:
            return None
        flags = _u32(data, 0x14)
        offset = HEADER_SIZE
        paths: list[str] = []
        shell_name = ""
        if flags & HAS_ID_LIST:
            size = _u16(data, offset)
            shell_name = _shell_name(data[offset + 2 : offset + 2 + size])
            offset += 2 + size
        if flags & HAS_LINK_INFO:
            size = _u32(data, offset)
            paths.append(_link_info_path(data[offset : offset + size]))
            offset += size
        width = 2 if flags & IS_UNICODE else 1
        encoding = "utf-16-le" if flags & IS_UNICODE else "cp1252"
        strings: dict[int, str] = {}
        for flag in (HAS_NAME, HAS_RELATIVE_PATH, HAS_WORKING_DIR, HAS_ARGUMENTS, HAS_ICON):
            if flags & flag:
                length = _u16(data, offset) * width
                strings[flag] = data[offset + 2 : offset + 2 + length].decode(encoding, errors="replace")
                offset += 2 + length
        if strings.get(HAS_RELATIVE_PATH):
            paths.append(strings[HAS_RELATIVE_PATH])
        advertised = bool(flags & HAS_DARWIN_ID)
        while offset + 8 <= len(data):
            size = _u32(data, offset)
            if size < 8:
                break
            signature = _u32(data, offset + 4)
            if signature == BLOCK_ENVIRONMENT:
                paths.append(_environment_path(data[offset : offset + size]))
            advertised = advertised or signature == BLOCK_DARWIN
            offset += size
    except (struct.error, ValueError, IndexError):
        return None
    unique = tuple(dict.fromkeys(p.strip() for p in paths if p.strip()))
    return LinkTarget(unique, shell_name, strings.get(HAS_ARGUMENTS, "").strip(), advertised)


def read(path: str) -> LinkTarget | None:
    try:
        with Path(path).open("rb") as file:
            return parse(file.read(MAX_BYTES))
    except OSError:
        return None
