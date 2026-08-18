# -*- coding: utf-8 -*-
"""Agente de priorizacion de inspecciones, construido sobre la API de Claude.

El agente atiende en lenguaje natural las preguntas de un jefe de taller sobre
que camiones inspeccionar. No decide nada por su cuenta: la decision la produce
el modelo entrenado en `02_modelado.ipynb`, y el agente se limita a consultarla,
explicarla y traducirla a terminos operativos.

Uso desde la linea de comandos:

    python src/agente.py                      # conversacion interactiva
    python src/agente.py "cuantos camiones hay que revisar"

Uso desde un cuaderno:

    from agente import Agente
    a = Agente()
    print(a.preguntar("por que el T00869"))
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
import herramientas  # noqa: E402

MODELO = "claude-sonnet-5"
MAX_TOKENS = 1500
MAX_VUELTAS = 8   # cota de seguridad para el ciclo de herramientas

INSTRUCCIONES = """\
Eres el asistente de un taller de mantenimiento de camiones pesados. Ayudas al
jefe de taller a decidir a que vehiculos enviar un mecanico para revisar el
sistema de aire comprimido (APS).

COMO TRABAJAS

Las decisiones no las tomas tu. Las produce un modelo entrenado sobre datos
historicos de la flota, y tu funcion es consultarlo, explicar sus resultados y
traducirlos a terminos que el taller pueda usar.

REGLA PRINCIPAL: toda cifra que menciones debe provenir de una herramienta.
Nunca estimes, aproximes, redondees de memoria ni calcules por tu cuenta un
numero que una herramienta puede darte. Si necesitas un dato, pide la
herramienta correspondiente. Si aun asi no esta disponible, dilo con claridad en
lugar de ofrecer una aproximacion.

Si una herramienta devuelve un error o indica que no encontro algo, comunicalo
tal cual. No inventes un resultado plausible ni supongas que el usuario se
equivoco al escribir, salvo que la herramienta lo sugiera.

COMO HABLAS

Te diriges a personal de taller, no a analistas. Habla de camiones, mecanicos,
inspecciones y costos. Se breve y concreto: quien pregunta esta organizando el
trabajo del dia.

Dos cosas que debes explicar cuando vengan al caso, porque generan confusion:

1. El sistema ordena muchas inspecciones que no encuentran nada, y eso es
   deliberado. Una averia en ruta cuesta cincuenta veces una revision
   innecesaria, asi que conviene revisar de mas. Si el usuario juzga el sistema
   por la proporcion de aciertos, reencuadra la conversacion hacia el costo
   total.

2. El puntaje de riesgo NO es una probabilidad. Nunca lo presentes como
   porcentaje ni digas "tiene un 80% de probabilidad de fallar". Es un valor que
   se compara contra un umbral, y sirve para ordenar camiones entre si.

3. Muchos camiones tienen el puntaje en el tope de la escala. Cuando la
   herramienta indique que esta saturado, no uses "veces el umbral" para
   comparar unos con otros: ese valor llega a su maximo y sale identico para
   todos, lo que sugeriria que camiones muy distintos son igual de urgentes.
   Para comparar urgencia usa la posicion en la flota.

LIMITES

El sistema prioriza, no diagnostica: indica que camion revisar y que lecturas lo
motivan, no que componente esta averiado. Si te preguntan que esta fallando en
un camion, aclara la diferencia.

Al comunicar las lecturas que motivan un aviso, usa unicamente las que la
herramienta devuelve como "hacia_inspeccionar". Las de
"en_contra_de_inspeccionar" empujan el puntaje a la baja: presentarlas como
motivo de la revision mandaria al mecanico a mirar justamente las lecturas que
indicaban que el vehiculo estaba bien. Si no hay ninguna en la primera lista,
dilo en lugar de rellenar con las otras.

Los nombres de los sensores estan anonimizados. No inventes que mide cada uno ni
sugieras interpretaciones fisicas que no puedes sostener.

La falla registrada de un camion solo esta disponible de forma retrospectiva,
para evaluar que tan bien funciono el sistema. No la uses para responder si un
camion concreto tiene o no una averia: en el momento de decidir, ese dato no
existe.
"""


@dataclass
class Agente:
    """Conversacion con memoria y acceso a las herramientas del sistema."""

    modelo: str = MODELO
    cliente: Anthropic | None = None
    historial: list[dict] = field(default_factory=list)
    registro_llamadas: list[dict] = field(default_factory=list)
    verboso: bool = False

    def __post_init__(self):
        if self.cliente is None:
            # El .env vive en la raiz del proyecto. Se localiza a partir de la
            # ubicacion de este archivo y no del directorio desde el que se
            # ejecute, que puede ser cualquiera.
            raiz = Path(__file__).resolve().parent.parent
            load_dotenv(raiz / ".env")
            load_dotenv()  # por si se definio en otro sitio
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    f"No se encontro ANTHROPIC_API_KEY. Definala en {raiz / '.env'} "
                    "con la linea ANTHROPIC_API_KEY=sk-ant-... sin comillas."
                )
            self.cliente = Anthropic()

    # -- ciclo principal ---------------------------------------------------

    def preguntar(self, mensaje: str) -> str:
        """Envia una pregunta y devuelve la respuesta final en texto."""
        self.historial.append({"role": "user", "content": mensaje})

        for _ in range(MAX_VUELTAS):
            respuesta = self.cliente.messages.create(
                model=self.modelo,
                max_tokens=MAX_TOKENS,
                system=INSTRUCCIONES,
                tools=herramientas.ESQUEMAS,
                messages=self.historial,
            )
            self.historial.append(
                {"role": "assistant", "content": respuesta.content}
            )

            if respuesta.stop_reason != "tool_use":
                return self._texto(respuesta.content)

            resultados = []
            for bloque in respuesta.content:
                if bloque.type != "tool_use":
                    continue
                if self.verboso:
                    print(f"  [herramienta] {bloque.name}({bloque.input})")
                salida = herramientas.ejecutar(bloque.name, dict(bloque.input))
                self.registro_llamadas.append(
                    {"herramienta": bloque.name, "argumentos": dict(bloque.input)}
                )
                resultados.append({
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": json.dumps(salida, ensure_ascii=False, default=str),
                })
            self.historial.append({"role": "user", "content": resultados})

        return ("Se alcanzo el numero maximo de consultas a las herramientas sin "
                "llegar a una respuesta. Reformule la pregunta de forma mas concreta.")

    @staticmethod
    def _texto(contenido) -> str:
        return "\n".join(b.text for b in contenido if b.type == "text").strip()

    def reiniciar(self):
        """Olvida la conversacion, conserva el registro de herramientas usadas."""
        self.historial = []

    def herramientas_usadas(self) -> list[str]:
        return [ll["herramienta"] for ll in self.registro_llamadas]


# ---------------------------------------------------------------------------
# Linea de comandos
# ---------------------------------------------------------------------------

BIENVENIDA = """\
Asistente de priorizacion de inspecciones (APS)
Escriba su consulta. 'salir' para terminar, 'reiniciar' para empezar de nuevo.
"""


def main():
    try:
        agente = Agente(verboso=True)
    except RuntimeError as e:
        print(e)
        return 1

    if len(sys.argv) > 1:
        print(agente.preguntar(" ".join(sys.argv[1:])))
        return 0

    print(BIENVENIDA)
    while True:
        try:
            entrada = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not entrada:
            continue
        if entrada.lower() in {"salir", "exit", "quit"}:
            return 0
        if entrada.lower() == "reiniciar":
            agente.reiniciar()
            print("Conversacion reiniciada.\n")
            continue
        try:
            print("\n" + agente.preguntar(entrada) + "\n")
        except Exception as e:  # noqa: BLE001
            print(f"\nError al consultar el modelo: {e}\n")


if __name__ == "__main__":
    raise SystemExit(main())
