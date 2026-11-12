"""Sesión de trabajo: una tarea del agente a la vez, una cola detrás y el botón del pánico por encima.

La lectura de órdenes y la tarea en curso van en paralelo, así que `/parar` y el atajo global funcionan a mitad de un
bucle o esperando al modelo. `panic` es síncrono, no espera a nada y se llama siempre desde el hilo del bucle de eventos
(el atajo global entra con `call_soon_threadsafe`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.agent import Agent, AgentResult
from core.llm.base import LLMError
from core.metrics import MetricsLog, describe

log = logging.getLogger(__name__)

SALIR = frozenset({"salir", "exit", "quit"})
PARAR = "/parar"
PARAR_ALIAS = frozenset({PARAR, "/para"})  # "/para" es el mismo botón: se escribe a menudo
NUEVO = "/nuevo"
AFIRMA = frozenset({"s", "si", "sí", "y", "yes"})
RELEASE_TIMEOUT_SECONDS = 5.0


class Session:
    def __init__(
        self,
        write: Callable[[str], None],
        release: Callable[[], Awaitable[Any]] | None = None,
        *,
        metrics: MetricsLog | None = None,
        show_metrics: bool = False,
    ) -> None:
        self._write = write
        self._metrics = metrics  # una línea JSON por turno en logs/
        self._show_metrics = show_metrics  # y otra legible en la consola
        self._release = release  # suelta lo que retenga hands (el navegador), sin cerrar ventanas
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._current: asyncio.Task[AgentResult] | None = None
        self._answer: asyncio.Future[bool] | None = None
        self._releasing: set[asyncio.Task[None]] = set()

    @property
    def busy(self) -> bool:
        return self._current is not None and not self._current.done()

    async def confirm(self, tool: str, args: dict[str, Any]) -> bool:
        """La siguiente línea que escriba el usuario es la respuesta. Se pregunta aquí para no competir con `read`."""
        self._answer = asyncio.get_running_loop().create_future()
        self._write(f"  ⚠ Confirmar {tool}({args})? [s/N]")
        try:
            return await self._answer
        finally:
            self._answer = None

    def panic(self) -> None:
        """Cancela la tarea en curso, vacía la cola y suelta el navegador. Se puede llamar en cualquier momento."""
        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
            dropped += 1
        running = self.busy
        if running and self._current is not None:
            self._current.cancel()
        extra = f" y {dropped} en cola" if dropped else ""
        self._write(f"Parado: tarea cancelada{extra}." if running else f"Parado: no había nada en marcha{extra}.")
        if self._release is not None:
            task = asyncio.get_running_loop().create_task(self._release_hands())
            self._releasing.add(task)
            task.add_done_callback(self._releasing.discard)

    async def _release_hands(self) -> None:
        assert self._release is not None
        try:
            await asyncio.wait_for(self._release(), RELEASE_TIMEOUT_SECONDS)
        except Exception as exc:  # un fallo al soltar no puede tumbar la sesión
            log.warning("no se pudo soltar el navegador", exc_info=True)
            self._write(f"Aviso: no he podido soltar el navegador ({type(exc).__name__}).")

    async def _run_one(self, agent: Agent, line: str) -> None:
        self._current = asyncio.create_task(agent.run(line))
        task = self._current
        await asyncio.wait({task})  # no se propaga la cancelación: el trabajador sigue vivo tras un pánico
        self._current = None
        if task.cancelled():
            return
        exc = task.exception()
        if isinstance(exc, LLMError):
            self._write(f"Error del modelo: {exc}")
        elif exc is not None:
            self._write(f"Error: {type(exc).__name__}: {exc}")
        else:
            result = task.result()
            self._write(f"{result.answer}  ({len(result.steps)} acciones · {result.seconds:.1f} s)")
            if self._show_metrics:
                self._write(f"  {describe(result.metrics)}")
            if self._metrics is not None:
                self._metrics.record(result.metrics)

    async def _work(self, agent: Agent) -> None:
        while True:
            line = await self._queue.get()
            try:
                if line.lower() == NUEVO:
                    agent.reset()
                    self._write("Conversación reiniciada.")
                elif (reply := agent.command(line)) is not None:
                    self._write(reply)  # recetas, alias y modo enseñar: sin modelo, en orden con las tareas
                else:
                    await self._run_one(agent, line)
            finally:
                self._queue.task_done()

    async def serve(self, agent: Agent, read: Callable[[], Awaitable[str]]) -> None:
        """Lee órdenes hasta `salir` o fin de la entrada y deja terminar lo que quede en la cola."""
        worker = asyncio.create_task(self._work(agent))
        try:
            while True:
                try:
                    line = (await read()).strip()
                except EOFError:
                    break
                if not line:
                    continue
                lowered = line.lower()
                if lowered in PARAR_ALIAS:
                    self.panic()
                elif self._answer is not None and not self._answer.done():
                    self._answer.set_result(lowered in AFIRMA)
                elif lowered in SALIR:
                    break
                else:
                    self._queue.put_nowait(line)
            await self._queue.join()
        finally:
            worker.cancel()
            await asyncio.gather(worker, *self._releasing, return_exceptions=True)
            agent.flush()  # «salir» o cerrar el CLI: la receta del último turno, si nadie la corrigió, se guarda
