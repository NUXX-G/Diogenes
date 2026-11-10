r"""¿Está este ejecutable firmado por Microsoft y es del sistema? Se usa para no tratar `notepad.exe` como desconocido.

Solo cuentan los ejecutables dentro de `System32`, `SysWOW64` o `Program Files\WindowsApps`: un `.exe` firmado que esté
en el escritorio o en Descargas sigue pidiendo confirmación. La firma se lee con `Get-AuthenticodeSignature`, que
entiende también las firmas por catálogo con las que Windows firma `System32`.
"""

from __future__ import annotations

import ntpath
import os
import subprocess
import sys

SYSTEM_SUBDIRS = (("System32",), ("SysWOW64",))
TRUSTED_SUBJECT = "O=Microsoft Corporation"
_CHECK = (
    "$s = Get-AuthenticodeSignature -LiteralPath $env:DIOGENES_SIGCHECK_PATH; "
    "Write-Output $s.Status; Write-Output $s.SignerCertificate.Subject"
)


def system_roots() -> tuple[str, ...]:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (
        ntpath.join(root, "System32"),
        ntpath.join(root, "SysWOW64"),
        ntpath.join(program_files, "WindowsApps"),
    )


def in_system_dir(path: str, roots: tuple[str, ...] | None = None) -> bool:
    candidate = ntpath.normcase(ntpath.normpath(path))
    return any(candidate.startswith(ntpath.normcase(ntpath.normpath(r)) + "\\") for r in roots or system_roots())


def parse_status(output: str) -> bool:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return len(lines) >= 2 and lines[0].lower() == "valid" and TRUSTED_SUBJECT in lines[1]


def is_signed_by_microsoft(path: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _CHECK],
            capture_output=True,
            check=False,
            timeout=20,
            env={**os.environ, "DIOGENES_SIGCHECK_PATH": path},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return parse_status(done.stdout.decode("utf-8", errors="replace"))


def is_trusted_system_app(path: str) -> bool:
    """Un ejecutable del sistema (no uno cualquiera que alguien dejó ahí) con firma válida de Microsoft."""
    return in_system_dir(path) and is_signed_by_microsoft(path)
