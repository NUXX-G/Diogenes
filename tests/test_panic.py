"""Botón del pánico: cancela a mitad de un bucle o esperando al modelo, vacía la cola y suelta el navegador."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Self

import pytest
from mcp import Client

from core.agent import PANIC_TOOL, Agent
from core.hotkey import DEFAULT_COMBO, WM_HOTKEY, HotkeyListener, load_combo, parse_combo
from core.intent import IntentGate
from core.llm.base import LLMResponse, Message, ToolSpec
from core.mcp_client import McpTools
from core.safety import ConfirmationPolicy
from core.session import Session
from hands.apps.index import AppIndex
from hands.browser.service import BrowserService
from hands.browser.session import BrowserSession
from hands.server import build_server
from tests.test_agent import FakeBackend, decir, llamar
from tests.test_browser import FakePage

POLICY = ConfirmationPolicy([])
PLAZO = 1.0  # "al instante" con margen para una máquina cargada; el modelo falso tardaría una hora


class SlowLLM:
    """Responde lo predefinido y, cuando se le acaba, se queda colgado como un modelo lento en CPU."""

    def __init__(self, *responses: LLMResponse) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.waiting = asyncio.Event()
        self.cancelled = False

    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        self.waiting.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("el modelo no debería terminar")


class SlowBackend(FakeBackend):
    """La herramienta `apps.search` tarda una hora; las demás responden al momento."""

    def __init__(self) -> None:
        super().__init__(names=("apps.search", "apps.launch", PANIC_TOOL))
        self.in_tool = asyncio.Event()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "apps.search":
            self.calls.append((name, arguments))
            self.in_tool.set()
            await asyncio.sleep(3600)
        return await super().call(name, arguments)


class Consola:
    """Una consola de verdad: leer espera a que se escriba algo y no bloquea el bucle."""

    def __init__(self) -> None:
        self.lineas: asyncio.Queue[str | None] = asyncio.Queue()
        self.salidas: list[str] = []

    async def read(self) -> str:
        line = await self.lineas.get()
        if line is None:
            raise EOFError
        return line

    def escribe(self, *lineas: str) -> None:
        for line in lineas:
            self.lineas.put_nowait(line)

    async def espera_salida(self, texto: str) -> None:
        async with asyncio.timeout(PLAZO):
            while not any(texto in s for s in self.salidas):
                await asyncio.sleep(0.005)


class Montaje:
    def __init__(self, llm: Any, backend: Any = None, release: Callable[[], Awaitable[Any]] | None = None) -> None:
        self.consola = Consola()
        self.liberado = 0
        self.backend = backend or FakeBackend()

        async def liberar() -> None:
            self.liberado += 1

        self.session = Session(self.consola.salidas.append, release or liberar)
        self.agent = Agent(llm, self.backend, policy=POLICY, intent=IntentGate.load(), confirm=self.session.confirm)

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(self.session.serve(self.agent, self.consola.read))
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.consola.lineas.put_nowait(None)
        async with asyncio.timeout(PLAZO):
            await self._task


async def test_cancela_esperando_al_modelo_al_instante() -> None:
    llm = SlowLLM()
    async with Montaje(llm) as m:
        m.consola.escribe("abre algo")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        inicio = time.monotonic()
        m.session.panic()
        await m.consola.espera_salida("Parado")
        await asyncio.sleep(0.01)
        assert time.monotonic() - inicio < PLAZO
        assert llm.cancelled is True and m.session.busy is False


async def test_cancela_a_mitad_del_bucle_y_no_vuelve_al_modelo() -> None:
    backend = SlowBackend()
    llm = SlowLLM(llamar("apps_search", nombre="x"))
    async with Montaje(llm, backend) as m:
        m.consola.escribe("abre algo")
        await asyncio.wait_for(backend.in_tool.wait(), PLAZO)
        m.session.panic()
        await m.consola.espera_salida("tarea cancelada")
        await asyncio.sleep(0.01)
    assert llm.calls == 1


async def test_vacia_la_cola_y_lo_cuenta() -> None:
    llm = SlowLLM()
    async with Montaje(llm) as m:
        m.consola.escribe("uno", "dos", "tres")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        await asyncio.sleep(0.01)
        m.session.panic()
        await m.consola.espera_salida("2 en cola")
        await asyncio.sleep(0.01)
    assert llm.calls == 1


async def test_parar_escrito_mientras_trabaja() -> None:
    llm = SlowLLM()
    async with Montaje(llm) as m:
        m.consola.escribe("abre algo")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        m.consola.escribe("/parar")
        await m.consola.espera_salida("tarea cancelada")
    assert llm.cancelled is True and m.liberado == 1


async def test_cancela_esperando_una_confirmacion_y_no_se_come_la_siguiente_orden() -> None:
    pendiente = json.dumps({"ok": False, "requiere_confirmacion": True, "objetivo": "x", "motivo": "y"})
    backend = FakeBackend({"apps.launch": pendiente})
    llm = SlowLLM(llamar("apps_launch", id="x"), decir("hola de nuevo"))
    async with Montaje(llm, backend) as m:
        m.consola.escribe("abre algo")
        await m.consola.espera_salida("Confirmar")
        m.session.panic()
        await m.consola.espera_salida("tarea cancelada")
        m.consola.escribe("saluda")
        await m.consola.espera_salida("hola de nuevo")
    assert [c for c in backend.calls if "confirmado" in c[1]] == []


async def test_tras_el_panico_se_puede_seguir_y_el_historial_queda_limpio() -> None:
    llm = SlowLLM()
    async with Montaje(llm) as m:
        m.consola.escribe("abre algo")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        m.session.panic()
        await m.consola.espera_salida("tarea cancelada")
        await asyncio.sleep(0.01)
        assert m.agent.history == ()
        llm._responses.append(decir("sigo aquí"))
        m.consola.escribe("hola")
        await m.consola.espera_salida("sigo aquí")


async def test_un_si_despues_del_panico_no_reanuda_lo_cancelado() -> None:
    llm = SlowLLM(decir("¿Abro la calculadora?"))
    async with Montaje(llm) as m:
        m.consola.escribe("necesito sumar")
        await m.consola.espera_salida("¿Abro la calculadora?")
        m.consola.escribe("otra cosa")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        m.session.panic()
        await m.consola.espera_salida("tarea cancelada")
        await asyncio.sleep(0.01)
        assert m.agent._last_answer == "" and m.agent._last_user == ""  # type: ignore[attr-defined]


async def test_sin_tarea_en_marcha_lo_dice_y_suelta_igual() -> None:
    async with Montaje(SlowLLM()) as m:
        m.session.panic()
        await m.consola.espera_salida("no había nada")
        await asyncio.sleep(0.01)
        assert m.liberado == 1


async def test_si_soltar_el_navegador_falla_la_sesion_sigue() -> None:
    async def rota() -> None:
        raise RuntimeError("hands caído")

    llm = SlowLLM(decir("sigo"))
    async with Montaje(llm, release=rota) as m:
        m.session.panic()
        await m.consola.espera_salida("no he podido soltar")
        m.consola.escribe("hola")
        await m.consola.espera_salida("sigo")


async def test_el_modelo_no_ve_la_herramienta_del_panico() -> None:
    backend = FakeBackend(names=("apps.search", PANIC_TOOL))
    seen: list[list[ToolSpec]] = []

    class Espia:
        async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
            seen.append(tools)
            return decir("ok")

    await Agent(Espia(), backend, policy=POLICY).run("hola")
    assert [t.name for t in seen[0]] == ["apps_search"]


# --- hands: cancela lo que esté en marcha y suelta el navegador ---------------------------------------------------


class SlowPage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.en_goto = asyncio.Event()
        self.cerrada = False

    async def goto(self, url: str) -> None:
        self.en_goto.set()
        await asyncio.sleep(3600)


class ReleasableProvider:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.released = 0

    async def page(self) -> FakePage:
        return self._page

    async def release(self) -> None:
        self.released += 1


async def test_hands_cancela_la_herramienta_lenta_y_suelta_el_navegador() -> None:
    page = SlowPage()
    provider = ReleasableProvider(page)
    async with Client(build_server(AppIndex(), browser=BrowserService(provider))) as client:
        lenta = asyncio.create_task(client.call_tool("browser.open", {"url": "https://ejemplo.es/"}))
        await asyncio.wait_for(page.en_goto.wait(), PLAZO)
        panico = await asyncio.wait_for(client.call_tool(PANIC_TOOL, {}), PLAZO)
        resultado = await asyncio.wait_for(lenta, PLAZO)
    assert panico.structured_content["ok"] is True and "1 acciones" in panico.structured_content["mensaje"]
    assert resultado.structured_content["cancelada"] is True
    assert provider.released == 1 and page.cerrada is False


async def test_el_cliente_mcp_sigue_valiendo_tras_cancelar_una_llamada() -> None:
    page = SlowPage()
    async with Client(build_server(AppIndex(), browser=BrowserService(ReleasableProvider(page)))) as client:
        tools = McpTools(client)
        lenta = asyncio.create_task(tools.call("browser.open", {"url": "https://ejemplo.es/"}))
        await asyncio.wait_for(page.en_goto.wait(), PLAZO)
        lenta.cancel()
        await asyncio.gather(lenta, return_exceptions=True)
        await asyncio.wait_for(tools.call(PANIC_TOOL, {}), PLAZO)
        assert await asyncio.wait_for(tools.tools(), PLAZO)


async def test_soltar_la_sesion_para_playwright_sin_cerrar_el_navegador() -> None:
    class Falso:
        def __init__(self) -> None:
            self.llamadas: list[str] = []

        async def stop(self) -> None:
            self.llamadas.append("stop")

        async def close(self) -> None:  # cerrar el Browser podría cerrar ventanas: no debe llamarse
            self.llamadas.append("close")

        def is_connected(self) -> bool:
            return True

    pw, browser = Falso(), Falso()
    session = BrowserSession()
    session._pw, session._browser, session._page = pw, browser, object()  # type: ignore[assignment]
    await session.release()
    assert pw.llamadas == ["stop"] and browser.llamadas == []
    assert session._pw is None and session._browser is None and session._page is None
    await session.release()  # dos pánicos seguidos no fallan


# --- atajo global -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("combo", "esperado"),
    [
        ("ctrl+alt+esc", (0x3, 0x1B)),
        ("Ctrl + Shift + F12", (0x6, 0x7B)),
        ("win+alt+p", (0x9, ord("P"))),
    ],
)
def test_interpreta_el_atajo(combo: str, esperado: tuple[int, int]) -> None:
    assert parse_combo(combo) == esperado


@pytest.mark.parametrize("combo", ["esc", "ctrl+alt", "ctrl+a+b", "ctrl+tecla_rara", ""])
def test_rechaza_atajos_invalidos(combo: str) -> None:
    with pytest.raises(ValueError):
        parse_combo(combo)


def test_el_atajo_sale_de_la_config(tmp_path: Path) -> None:
    (tmp_path / "atajos.json").write_text('{"boton_panico": "ctrl+shift+f9"}', encoding="utf-8")
    assert load_combo(tmp_path) == "ctrl+shift+f9"


def test_sin_config_o_con_atajo_invalido_queda_el_de_siempre(tmp_path: Path) -> None:
    assert load_combo(tmp_path) == DEFAULT_COMBO
    (tmp_path / "atajos.json").write_text('{"boton_panico": "esc"}', encoding="utf-8")
    assert load_combo(tmp_path) == DEFAULT_COMBO


def test_el_example_trae_ctrl_alt_esc() -> None:
    assert load_combo(Path("configuracion")) == "ctrl+alt+esc"


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey solo existe en Windows")
def test_el_hilo_del_atajo_avisa_al_recibir_la_tecla_y_se_detiene() -> None:
    import ctypes

    pulsado = threading.Event()
    listener = HotkeyListener("ctrl+alt+shift+f23", pulsado.set)  # un atajo que nadie usa
    if not listener.start():
        pytest.skip("otro programa tiene registrado el atajo de prueba")
    try:
        ctypes.WinDLL("user32").PostThreadMessageW(listener.thread_id, WM_HOTKEY, 1, 0)
        assert pulsado.wait(PLAZO)
    finally:
        listener.stop()
    assert not listener._thread.is_alive()  # type: ignore[union-attr]
