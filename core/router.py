"""Router: atajos deterministas que se resuelven sin llamar al LLM (teclas multimedia y volumen).

Son una optimización de velocidad, nunca el mecanismo principal: solo se activan cuando TODA la frase del usuario
es uno de los atajos configurados (`configuracion/atajos.json`, o el `.example`). "Pon música de Daft Punk en Spotify"
no coincide con ninguno y sigue al planificador.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hands.apps.models import normalize

CONFIG_DIR = Path("configuracion")
MEDIA_TOOL = "media.key"

ANSWERS = {
    "play_pausa": "Hecho: play/pausa.",
    "siguiente": "Siguiente.",
    "anterior": "Anterior.",
    "parar": "Parado.",
    "subir_volumen": "Volumen arriba.",
    "bajar_volumen": "Volumen abajo.",
    "silencio": "Silencio.",
}


@dataclass(frozen=True)
class Shortcut:
    tool: str
    arguments: dict[str, object]
    answer: str


class Router:
    def __init__(self, keys: dict[str, tuple[tuple[str, ...], int]], filler: tuple[str, ...]) -> None:
        self._phrases: dict[str, tuple[str, int]] = {}
        for key, (phrases, times) in keys.items():
            for phrase in phrases:
                self._phrases[normalize(phrase)] = (key, times)
        self._filler = tuple(sorted((normalize(f) for f in filler), key=len, reverse=True))

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> Router:
        for name in ("atajos.json", "atajos.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                keys = {key: (tuple(spec["frases"]), int(spec.get("veces", 1))) for key, spec in data["teclas"].items()}
                return cls(keys, tuple(data.get("relleno", [])))
        raise FileNotFoundError(f"No hay atajos.json ni atajos.example.json en {directory}")

    def _strip_filler(self, text: str) -> str:
        words = f" {normalize(text)} "
        for filler in self._filler:
            words = words.replace(f" {filler} ", " ")
        return " ".join(words.split())

    def match(self, text: str) -> Shortcut | None:
        found = self._phrases.get(self._strip_filler(text))
        if found is None:
            return None
        key, times = found
        return Shortcut(MEDIA_TOOL, {"tecla": key, "veces": times}, ANSWERS.get(key, "Hecho."))
