"""App Paths que son una app del menú Inicio (notepad → Bloc de notas) y ejecutables firmados del sistema."""

from __future__ import annotations

from pathlib import Path

from hands.apps import reconcile, signature
from hands.apps.cache import IndexCache
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.apps.policy import LaunchPolicy
from hands.apps.shortcuts import LinkTarget
from hands.system.closer import matches
from hands.system.windows import Win

APPX = AppEntry(
    "startapps:e56afe66", "Bloc de notas", "startapps", "shell", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
)
STORE_EXE = r"C:\Program Files\WindowsApps\Microsoft.WindowsNotepad_11.2607.14.0_x64__8wekyb3d8bbwe\Notepad\Notepad.exe"
REGISTRY = AppEntry("registry:notepad.exe", "notepad", "registry", "file", STORE_EXE)
POLICY = LaunchPolicy.from_list([".exe", ".bat", ".ps1"])


def no_links(_path: str) -> LinkTarget | None:
    return None


def test_app_paths_de_un_paquete_de_la_store_se_funde_con_su_app_del_menu_inicio() -> None:
    merged = reconcile.merge_app_paths([REGISTRY, APPX], no_links)
    assert [e.id for e in merged] == ["startapps:e56afe66"]
    assert merged[0].aliases == ("notepad",)


def test_abre_el_notepad_elige_la_app_del_menu_inicio_y_no_el_exe_de_app_paths() -> None:
    index = AppIndex()
    index.replace(reconcile.merge_app_paths([REGISTRY, APPX], no_links))
    found = index.search("notepad")
    assert found[0].entry.id == "startapps:e56afe66"
    assert len(found) == 1
    assert POLICY.assess(found[0].entry).requires is False  # shell: no es un ejecutable suelto


def test_app_paths_cuyo_exe_es_el_destino_de_un_acceso_del_menu_inicio_se_funde_con_el_acceso() -> None:
    lnk = AppEntry("startmenu:aaaa1111", "Mi Editor", "startmenu", "file", r"C:\ProgramData\Start Menu\Mi Editor.lnk")
    exe = AppEntry("registry:edit.exe", "edit", "registry", "file", r"C:\Apps\Editor\edit.exe")

    def read(_path: str) -> LinkTarget:
        return LinkTarget(paths=(r"c:\apps\editor\EDIT.EXE",))

    merged = reconcile.merge_app_paths([exe, lnk], read)
    assert [e.id for e in merged] == ["startmenu:aaaa1111"]
    assert merged[0].aliases == ("edit",)


def test_un_app_paths_sin_app_del_menu_inicio_se_queda_como_estaba() -> None:
    suelto = AppEntry("registry:tool.exe", "tool", "registry", "file", r"C:\Tools\tool.exe")
    assert reconcile.merge_app_paths([suelto, APPX], no_links) == [suelto, APPX]


def test_si_el_paquete_tiene_varias_apps_no_se_adivina_cual() -> None:
    otra = AppEntry("startapps:0b0b0b0b", "Otra", "startapps", "shell", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!Otra")
    merged = reconcile.merge_app_paths([REGISTRY, APPX, otra], no_links)
    assert REGISTRY in merged


def test_un_acceso_roto_no_tumba_la_fusion() -> None:
    lnk = AppEntry("startmenu:aaaa1111", "Roto", "startmenu", "file", r"C:\x\Roto.lnk")

    def read(_path: str) -> LinkTarget:
        raise ValueError("lnk corrupto")

    assert reconcile.merge_app_paths([REGISTRY, lnk], read) == [REGISTRY, lnk]


def test_los_alias_se_guardan_en_la_cache(tmp_path: Path) -> None:
    cache = IndexCache(tmp_path / "idx.json")
    cache.save([AppEntry("startapps:1", "Bloc de notas", "startapps", "shell", "x!y", ("notepad",))])
    assert cache.load() == [AppEntry("startapps:1", "Bloc de notas", "startapps", "shell", "x!y", ("notepad",))]


def test_el_nombre_del_ejecutable_tambien_localiza_la_ventana_al_cerrar() -> None:
    win = Win(1, 10, "Sin título", r"C:\Windows\System32\notepad.exe")
    assert matches(AppEntry("startapps:1", "Bloc de notas", "startapps", "shell", "x!y", ("notepad",)), win)


# --- ejecutables del sistema firmados -----------------------------------------------------------------------------


def test_app_paths_en_windowsapps_firmado_por_microsoft_no_pide_confirmacion() -> None:
    assert POLICY.assess(REGISTRY, is_system_app=lambda _p: True).requires is False


def test_app_paths_en_system32_sin_firma_valida_si_pide_confirmacion() -> None:
    sys32 = AppEntry("registry:x.exe", "x", "registry", "file", r"C:\Windows\System32\x.exe")
    assert POLICY.assess(sys32, is_system_app=lambda _p: False).requires is True


def test_un_exe_firmado_fuera_de_las_carpetas_del_sistema_sigue_pidiendo_confirmacion() -> None:
    fuera = AppEntry("registry:x.exe", "x", "registry", "file", r"C:\Users\yo\Downloads\x.exe")
    assert POLICY.assess(fuera, is_system_app=lambda _p: True).requires is True


def test_la_regla_del_sistema_solo_vale_para_app_paths() -> None:
    escritorio = AppEntry("desktop:1", "x", "desktop", "file", r"C:\Windows\System32\x.exe")
    assert POLICY.assess(escritorio, is_system_app=lambda _p: True).requires is True


def test_in_system_dir_no_se_deja_engañar_por_prefijos_parecidos() -> None:
    roots = (r"C:\Windows\System32",)
    assert signature.in_system_dir(r"c:\windows\system32\notepad.exe", roots)
    assert not signature.in_system_dir(r"C:\Windows\System32Fake\notepad.exe", roots)
    assert not signature.in_system_dir(r"C:\Windows\System32\..\..\Users\x.exe", roots)


def test_solo_vale_una_firma_valida_de_microsoft() -> None:
    assert signature.parse_status("Valid\nCN=Microsoft Windows, O=Microsoft Corporation, L=Redmond\n")
    assert not signature.parse_status("Valid\nCN=Otro, O=Otra Empresa\n")
    assert not signature.parse_status("NotSigned\n\n")
    assert not signature.parse_status("")
