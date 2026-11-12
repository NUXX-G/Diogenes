"""Un PC falso para medir y probar a Diogenes sin tocar el equipo: hands-mcp de verdad (`build_server`) con dobles
solo en lo más bajo (ventanas, sesiones multimedia, lanzador y un sitio de vídeos). Funciona también en Linux.

El sitio de vídeos imita la estructura típica (resultados → canal → pestaña Vídeos → ordenar por Populares → vídeo)
para cualquier canal que se busque: sirve para medir «dame el vídeo más visto del canal X» y para probar recetas
enseñadas con un canal y reproducidas con otro.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from mcp import Client

from hands.apps.index import AppIndex
from hands.apps.models import AppEntry, normalize
from hands.browser.service import BrowserService
from hands.server import build_server
from hands.system.closer import AppCloser, CloseConfig
from hands.system.media import Media
from hands.system.sessions import PAUSED, PLAYING, MediaSession, Sessions
from hands.system.windows import Win
from hands.web.fetch import FetchResult
from hands.web.opensearch import PLACEHOLDER
from hands.web.service import WebService
from hands.web.store import TemplateStore

HOST = "https://www.youtube.com"

APPS = (
    AppEntry("startapps:spotify", "Spotify", "startapps", "shell", "SpotifyAB.SpotifyMusic!Spotify"),
    AppEntry("startapps:calc", "Calculadora", "startapps", "shell", "Microsoft.WindowsCalculator!App"),
    AppEntry("startapps:bloc", "Bloc de notas", "startapps", "shell", "Microsoft.WindowsNotepad!App", ("notepad",)),
    AppEntry("steam:291550", "Brawlhalla", "steam", "uri", "steam://rungameid/291550"),
    AppEntry("startapps:vscode", "Visual Studio Code", "startapps", "shell", "Microsoft.VisualStudioCode"),
    AppEntry("startapps:clip", "Editor de vídeo", "startapps", "shell", "Clipchamp.Clipchamp!App"),
    AppEntry("startapps:regedit", "Editor del Registro", "startapps", "shell", "Microsoft.Regedit!App"),
    AppEntry("startapps:paint", "Paint", "startapps", "shell", "Microsoft.Paint!App"),
)
WINDOW_TITLES = {"startapps:calc": ("Calculadora", "CalculatorApp.exe"), "startapps:paint": ("Paint", "mspaint.exe")}


class FakeWindows:
    """Ventanas abiertas. Las apps lanzadas abren una; cerrar con WM_CLOSE la quita."""

    def __init__(self) -> None:
        self.windows: list[Win] = []
        self._next = 100

    def open(self, entry: AppEntry) -> None:
        title, exe = WINDOW_TITLES.get(entry.id, (entry.name, f"{normalize(entry.name).replace(' ', '')}.exe"))
        self._next += 1
        self.windows.append(Win(self._next, 4000 + self._next, title, f"C:\\Apps\\{exe}"))

    def list_windows(self) -> list[Win]:
        return list(self.windows)

    def post_close(self, hwnd: int) -> None:
        self.windows = [w for w in self.windows if w.hwnd != hwnd]

    def terminate(self, pid: int) -> bool:
        self.windows = [w for w in self.windows if w.pid != pid]
        return True


class FakeMediaBackend:
    """Una sesión multimedia (el navegador reproduciendo un vídeo) que obedece de verdad."""

    def __init__(self) -> None:
        self.found = [MediaSession("chrome", "Chrome", "Mochis fáciles", "Canal Cocina", PLAYING)]

    async def sessions(self) -> list[MediaSession]:
        return list(self.found)

    async def act(self, session_id: str, action: str) -> bool:
        for i, s in enumerate(self.found):
            if s.id == session_id:
                status = {"pause": PAUSED, "play": PLAYING}.get(action)
                if action == "toggle":
                    status = PAUSED if s.status == PLAYING else PLAYING
                if status is not None:
                    self.found[i] = replace(s, status=status)
        return True


class FakeKeys:
    def __init__(self) -> None:
        self.pressed: list[int] = []

    def press(self, vk: int) -> None:
        self.pressed.append(vk)


@dataclass
class _Link:
    role: str
    name: str
    href: str = ""
    action: str = ""  # "sort" para el chip de ordenar


@dataclass
class VideoSite:
    """Sitio de vídeos falso. Cada canal tiene 5 vídeos; el más visto NO es el más reciente."""

    url: str = "about:blank"
    title: str = ""
    sorted_by_views: bool = False
    visits: list[str] = field(default_factory=list)
    # Cómo enlaza cada canal la página de resultados (como YouTube, que no siempre lo escribe igual):
    # "plano" → «Hrom»; "ir" → «Ir al canal Hrom»; "handle" → «@LofiGirl · 1,5 M de suscriptores».
    layouts: dict[str, str] = field(default_factory=lambda: {"hrom": "ir", "lofigirl": "handle"})
    _links: dict[str, _Link] = field(default_factory=dict)

    @staticmethod
    def videos(channel: str) -> list[tuple[str, int]]:
        names = ("directo de verano", "récord absoluto", "tutorial rápido", "respuesta a todo", "trailer")
        views = (1200, 98000, 5400, 300, 2100)
        return [(f"{channel}: {n}", v) for n, v in zip(names, views, strict=True)]

    @staticmethod
    def slug(channel: str) -> str:
        return normalize(channel).replace(" ", "")

    def _channel_of(self, path: str) -> str:
        return path.split("/")[1].removeprefix("@") if path.startswith("/@") else ""

    def _render(self) -> str:
        parts = urlsplit(self.url)
        path = parts.path
        links: list[_Link] = [_Link("searchbox", "Buscar")]
        if path == "/results":
            q = parse_qs(parts.query).get("search_query", [""])[0]
            self.title = f"{q} - YouTube"
            layout = self.layouts.get(self.slug(q), "plano")
            handle = "".join(w.capitalize() for w in normalize(q).split())
            channel: _Link | None = {
                "plano": _Link("link", q, f"/@{self.slug(q)}"),
                "ir": _Link("link", f"Ir al canal {q}", f"/@{self.slug(q)}"),
                "handle": _Link("link", f"@{handle} · 1,5 M de suscriptores", f"/@{handle}"),
                "sin": None,  # el canal no sale en los resultados
            }[layout]
            links += [_Link("link", f"{q} - mejores momentos", "/watch?v=r1")]
            links += [channel] if channel else []
            links += [_Link("link", f"Reacción a {q}", "/watch?v=r2")]
        elif path.startswith("/@"):
            slug = self._channel_of(path)
            self.title = f"{slug} - YouTube"
            links += [
                _Link("tab", "Inicio", f"/@{slug}"),
                _Link("tab", "Vídeos", f"/@{slug}/videos"),
                _Link("tab", "Shorts", f"/@{slug}/shorts"),
            ]
            if path.endswith("/videos"):
                links += [_Link("button", "Más recientes"), _Link("button", "Populares", action="sort")]
                listed = self.videos(slug)
                if self.sorted_by_views:
                    listed = sorted(listed, key=lambda v: -v[1])
                links += [_Link("link", f"{n} · {v} visualizaciones", f"/watch?v={quote(n)}") for n, v in listed]
        elif path == "/watch":
            self.title = f"{parse_qs(parts.query).get('v', [''])[0]} - YouTube"
            links += [_Link("button", "Me gusta"), _Link("link", "Siguiente vídeo", "/watch?v=next")]
        self._links = {f"e{i}": link for i, link in enumerate(links, start=1)}
        lines = ["- main:"]
        for ref, link in self._links.items():
            lines.append(f'  - {link.role} "{link.name}" [ref={ref}] [cursor=pointer]:')
            if link.href:
                lines.append(f"    - /url: {link.href}")
        return "\n".join(lines)

    async def goto(self, url: str) -> None:
        self.visits.append(url)
        self.url, self.sorted_by_views = url, False

    async def current(self) -> tuple[str, str]:
        self._render()
        return self.url, self.title

    async def aria(self) -> str:
        return self._render()

    async def click(self, ref: str) -> None:
        link = self._links.get(ref)
        if link is None:
            return
        if link.action == "sort":
            self.sorted_by_views = True
        elif link.href:
            await self.goto(HOST + link.href)

    async def fill(self, ref: str, text: str, enter: bool) -> None:
        if enter:
            await self.goto(f"{HOST}/results?search_query={quote(text)}")

    async def is_sensitive(self, ref: str) -> bool:
        return False

    async def is_multiline(self, ref: str) -> bool:
        return False

    async def href(self, ref: str) -> str:
        link = self._links.get(ref)
        return HOST + link.href if link and link.href else ""

    async def back(self) -> None: ...


class _Provider:
    def __init__(self, page: VideoSite) -> None:
        self._page = page

    async def page(self) -> VideoSite:
        return self._page

    async def release(self) -> None: ...


class _NoNetwork:
    async def __call__(self, url: str) -> FetchResult:
        raise httpx.ConnectError(f"sin red en el banco de pruebas: {url}")


async def _no_sleep(_s: float) -> None:
    return None


@dataclass
class FakePC:
    """Lo que ha pasado en el PC falso, para comprobarlo en los tests."""

    windows: FakeWindows = field(default_factory=FakeWindows)
    media: FakeMediaBackend = field(default_factory=FakeMediaBackend)
    site: VideoSite = field(default_factory=VideoSite)
    keys: FakeKeys = field(default_factory=FakeKeys)
    launched: list[str] = field(default_factory=list)

    def launch(self, entry: AppEntry) -> None:
        self.launched.append(entry.id)
        self.windows.open(entry)

    def server(self) -> Any:
        index = AppIndex()
        index.replace(APPS)
        store = TemplateStore(":memory:")
        store.save("youtube", HOST, f"{HOST}/results?search_query={PLACEHOLDER}", "opensearch")
        browser = BrowserService(_Provider(self.site))
        web = WebService(store, _NoNetwork(), opener=lambda _u: None, strategies=[], browser=browser)
        config = CloseConfig(confirm_soft_close=True, wait_seconds=0.0, protected=frozenset())
        closer = AppCloser(self.windows, config, sleep=lambda _s: None, is_dir=lambda _p: False)
        media = Media(self.keys)
        sessions = Sessions(self.media, press_key=media.key, sleep=_no_sleep, verify_seconds=0.0)
        return build_server(
            index, launch=self.launch, web=web, browser=browser, closer=closer, media=media, sessions=sessions
        )


def client(pc: FakePC) -> Client:
    return Client(pc.server())


def pretty(output: str) -> str:
    try:
        return json.dumps(json.loads(output), ensure_ascii=False)[:200]
    except json.JSONDecodeError:
        return output[:200]
