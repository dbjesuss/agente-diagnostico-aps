# -*- coding: utf-8 -*-
"""Herramientas del agente de priorizacion de inspecciones.

Cada funcion publica de este modulo es una herramienta que el agente puede
invocar. Todas leen los artefactos producidos por el cuaderno de modelado y
devuelven diccionarios serializables.

Principio de diseno: **ninguna cifra se calcula en el modelo de lenguaje**. El
agente formula la consulta, estas funciones producen los numeros y el agente los
comunica. Si un dato no esta disponible, la herramienta lo dice de forma
explicita en lugar de aproximarlo.

Separacion entre operacion y evaluacion: las herramientas de uso diario no
exponen la falla real registrada, porque en operacion esa informacion no existe
todavia. Solo `evaluar_desempeno` la utiliza, y lo hace de forma retrospectiva.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

DIR_MODELOS = Path(__file__).resolve().parent.parent / "models"

# Columnas de la flota que nunca se exponen en herramientas de operacion
COLUMNAS_RESERVADAS = {"falla_real"}


class ArtefactoAusente(FileNotFoundError):
    """El cuaderno de modelado no se ha ejecutado, o no dejo sus salidas."""


def _ruta(nombre: str) -> Path:
    ruta = DIR_MODELOS / nombre
    if not ruta.exists():
        raise ArtefactoAusente(
            f"Falta el archivo {nombre} en {DIR_MODELOS}. "
            "Ejecute el cuaderno 02_modelado.ipynb para generarlo."
        )
    return ruta


@lru_cache(maxsize=1)
def _metadatos() -> dict:
    with open(_ruta("metadatos_aps.json"), encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _flota() -> pd.DataFrame:
    df = pd.read_csv(_ruta("flota_puntuada.csv"))
    return df.sort_values("riesgo", ascending=False).reset_index(drop=True)


@lru_cache(maxsize=1)
def _contribuciones() -> pd.DataFrame | None:
    """Contribucion de cada sensor al puntaje de cada camion.

    Artefacto opcional. Si no existe, los avisos se construyen con las pocas
    contribuciones que trae la flota puntuada.
    """
    ruta = DIR_MODELOS / "contribuciones_por_sensor.csv"
    if not ruta.exists():
        return None
    return pd.read_csv(ruta).set_index("camion_id")


def _umbral() -> float:
    return float(_metadatos()["umbral_inspeccion"]["valor"])


def _sensores_del_camion(camion_id: str, n: int = 5) -> dict | None:
    """Sensores que explican el puntaje de un camion, separados por direccion.

    Una contribucion positiva empuja el puntaje hacia arriba: es una razon para
    inspeccionar. Una negativa lo empuja hacia abajo. Mezclarlas produciria un
    aviso que manda al mecanico a revisar justamente las lecturas que indicaban
    que el vehiculo estaba bien, de modo que se devuelven por separado y solo se
    incluyen las que superan un aporte apreciable.
    """
    umbral_aporte = 0.01

    def _empaquetar(serie):
        suben = [
            {"sensor": str(s), "contribucion": round(float(v), 4)}
            for s, v in serie.items() if v > umbral_aporte
        ][:n]
        bajan = [
            {"sensor": str(s), "contribucion": round(float(v), 4)}
            for s, v in serie.sort_values().items() if v < -umbral_aporte
        ][:3]
        return {
            "hacia_inspeccionar": suben,
            "en_contra_de_inspeccionar": bajan,
            "nota": ("Solo las lecturas de 'hacia_inspeccionar' justifican el aviso. "
                     "Las otras empujan el puntaje a la baja y se incluyen unicamente "
                     "como contexto: no deben presentarse como motivo de la revision."),
        }

    contrib = _contribuciones()
    if contrib is not None and camion_id in contrib.index:
        return _empaquetar(contrib.loc[camion_id].sort_values(ascending=False))

    # Respaldo: las pocas contribuciones que trae la flota puntuada
    flota = _flota()
    fila = flota.loc[flota["camion_id"] == camion_id]
    if fila.empty:
        return None
    columnas = [c for c in flota.columns if c.startswith("shap_")]
    if not columnas:
        return None
    serie = fila[columnas].iloc[0]
    serie.index = [str(c).replace("shap_", "").rsplit("_", 1)[0] for c in serie.index]
    resultado = _empaquetar(serie.sort_values(ascending=False))
    resultado["cobertura"] = (
        "Solo se dispone de las contribuciones de las tres lecturas mas influyentes "
        "del conjunto, no de las 170. Ejecute la celda que genera "
        "contribuciones_por_sensor.csv para un desglose completo."
    )
    return resultado


# ---------------------------------------------------------------------------
# Herramientas
# ---------------------------------------------------------------------------

def resumen_flota() -> dict:
    """Estado general de la flota evaluada y costo de la politica recomendada."""
    flota = _flota()
    meta = _metadatos()
    senalados = int(flota["senalado"].sum())
    total = len(flota)
    return {
        "camiones_evaluados": total,
        "camiones_senalados": senalados,
        "porcentaje_de_la_flota": round(100 * senalados / total, 1),
        "umbral_de_riesgo": _umbral(),
        "costo_politica_recomendada": meta["desempeno"]["costo_total"],
        "costo_no_inspeccionar_a_nadie": meta["referencias"]["no_inspeccionar_a_nadie"],
        "costo_inspeccionar_a_todos": meta["referencias"]["inspeccionar_a_todos"],
        "reduccion_vs_correctivo_pct": meta["referencias"]["reduccion_vs_correctivo_pct"],
        "unidades": "Los costos estan en las unidades definidas por la operacion: "
                    "10 por inspeccion innecesaria, 500 por averia no detectada.",
    }


def consultar_camion(camion_id: str) -> dict:
    """Puntaje, decision y sensores que la motivan, para un camion concreto."""
    flota = _flota()
    fila = flota.loc[flota["camion_id"] == camion_id]
    if fila.empty:
        return {
            "encontrado": False,
            "camion_id": camion_id,
            "mensaje": f"No hay ningun camion con identificador {camion_id} en la "
                       f"flota evaluada. Los identificadores van de "
                       f"{flota['camion_id'].min()} a {flota['camion_id'].max()}.",
        }
    fila = fila.iloc[0]
    umbral = _umbral()
    riesgo = float(fila["riesgo"])
    posicion = int(flota.index[flota["camion_id"] == camion_id][0]) + 1
    saturado = riesgo > 0.999

    resultado = {
        "encontrado": True,
        "camion_id": camion_id,
        "puntaje_de_riesgo": round(riesgo, 6),
        "umbral_de_inspeccion": umbral,
        "decision": "INSPECCIONAR" if int(fila["senalado"]) == 1 else "no inspeccionar",
        "veces_el_umbral": round(riesgo / umbral, 1) if umbral else None,
        "posicion_en_la_flota": posicion,
        "total_camiones": len(flota),
        "sensores_que_motivan": _sensores_del_camion(camion_id),
        "advertencia": _metadatos()["umbral_inspeccion"]["advertencia"],
    }

    if saturado:
        resultado["puntaje_saturado"] = True
        resultado["nota_sobre_el_multiplo"] = (
            "El puntaje esta en el tope de la escala, de modo que 'veces el umbral' "
            "alcanza su valor maximo y es identico para todos los camiones saturados. "
            "No lo use para comparar urgencia entre ellos: el dato que si los ordena "
            "es la posicion en la flota."
        )
    return resultado


def priorizar_inspecciones(capacidad: int) -> dict:
    """Que camiones inspeccionar cuando la capacidad del taller es limitada.

    Devuelve los camiones de mayor puntaje hasta agotar la capacidad, e informa
    cuantos de los senalados por el sistema quedarian sin atender.
    """
    flota = _flota()
    total = len(flota)
    senalados = int(flota["senalado"].sum())

    if capacidad < 0:
        return {"error": "La capacidad debe ser un numero no negativo."}
    capacidad = min(int(capacidad), total)

    seleccion = flota.head(capacidad)
    corte = float(seleccion["riesgo"].min()) if capacidad > 0 else None

    resultado = {
        "capacidad_solicitada": capacidad,
        "camiones_senalados_por_el_sistema": senalados,
        "cubre_todos_los_senalados": capacidad >= senalados,
        "senalados_sin_atender": max(senalados - capacidad, 0),
        "puntaje_minimo_de_los_seleccionados": round(corte, 4) if corte is not None else None,
        "umbral_recomendado": _umbral(),
        "camiones": [
            {
                "camion_id": str(r.camion_id),
                "puntaje": round(float(r.riesgo), 6),
                "senalado_por_el_sistema": bool(r.senalado),
            }
            for r in seleccion.head(25).itertuples()
        ],
        "nota_lista": f"Se listan los primeros {min(capacidad, 25)} de {capacidad}.",
    }

    if capacidad < senalados:
        resultado["advertencia"] = (
            "La capacidad es menor que el numero de camiones que el sistema senala. "
            "Los no atendidos son los de menor puntaje entre los senalados, pero "
            "siguen estando por encima del umbral de inspeccion."
        )
    return resultado


def generar_orden_trabajo(camion_id: str) -> dict:
    """Datos de la orden de inspeccion que recibiria el mecanico."""
    consulta = consultar_camion(camion_id)
    if not consulta.get("encontrado"):
        return consulta
    if consulta["decision"] != "INSPECCIONAR":
        return {
            "camion_id": camion_id,
            "procede": False,
            "motivo": f"El puntaje de este camion ({consulta['puntaje_de_riesgo']}) esta "
                      f"por debajo del umbral de inspeccion ({consulta['umbral_de_inspeccion']}). "
                      "El sistema no recomienda inspeccionarlo.",
            "puntaje_de_riesgo": consulta["puntaje_de_riesgo"],
        }
    return {
        "camion_id": camion_id,
        "procede": True,
        "sistema_a_revisar": "Sistema de aire comprimido (APS)",
        "puntaje_de_riesgo": consulta["puntaje_de_riesgo"],
        "umbral_de_inspeccion": consulta["umbral_de_inspeccion"],
        "veces_el_umbral": consulta["veces_el_umbral"],
        "posicion_en_la_flota": consulta["posicion_en_la_flota"],
        "sensores_que_motivan": consulta["sensores_que_motivan"],
        "alcance": "Las lecturas indicadas senalan por donde empezar el diagnostico. "
                   "No identifican el componente averiado: el sistema prioriza, no "
                   "diagnostica.",
        "costo_de_referencia": {
            "inspeccion_innecesaria": _metadatos()["costos"]["inspeccion_innecesaria"],
            "averia_no_detectada": _metadatos()["costos"]["averia_no_detectada"],
        },
    }


def explicar_sistema(tema: str = "general") -> dict:
    """Como funciona el sistema, en que se apoya y que no puede hacer."""
    meta = _metadatos()
    temas = {
        "general": {
            "que_hace": meta["sistema"],
            "decision_que_apoya": meta["decision"],
            "variables_utilizadas": meta["n_variables"],
            "capacidad_de_ordenamiento_auc": meta["desempeno"]["auc_roc"],
        },
        "costos": {
            **meta["costos"],
            "interpretacion": "Una averia en ruta cuesta 50 veces una inspeccion "
                              "innecesaria. Por eso conviene inspeccionar de mas: la "
                              "politica que minimiza el gasto total genera muchas "
                              "revisiones que no encuentran nada.",
        },
        "umbral": meta["umbral_inspeccion"],
        "sensores": {
            "aporte_por_sensor_pct": meta["sensores_principales"],
            "precaucion": "Varias lecturas del registro contienen informacion "
                          "practicamente identica. Cuando eso ocurre, el reparto de "
                          "credito entre ellas es arbitrario: que un sensor no aparezca "
                          "en la lista no significa que no informe.",
        },
        "limitaciones": {
            "puntaje_no_es_probabilidad": meta["umbral_inspeccion"]["advertencia"],
            "senala_no_diagnostica": "El sistema indica que camion revisar y que "
                                     "lecturas lo motivan, no que componente fallo.",
            "variables_anonimizadas": "Los sensores aparecen con identificadores "
                                      "codificados. Sin el diccionario de variables no "
                                      "puede verificarse que la atribucion sea "
                                      "fisicamente razonable.",
            "senal_posiblemente_de_desgaste": "Parte de la senal podria provenir de la "
                                              "exposicion acumulada del vehiculo y no de "
                                              "un deterioro especifico del APS. Conviene "
                                              "verificarlo contra el significado real de "
                                              "los sensores antes de implantar.",
            "vigencia": "Los datos corresponden a un periodo concreto. Una desviacion "
                        "sostenida en la proporcion de camiones senalados indica que "
                        "las condiciones cambiaron y que conviene reentrenar.",
        },
    }
    if tema not in temas:
        return {
            "error": f"Tema no reconocido: {tema}.",
            "temas_disponibles": sorted(temas.keys()),
        }
    return {"tema": tema, "contenido": temas[tema]}


def evaluar_desempeno(capacidad: int | None = None) -> dict:
    """Resultado retrospectivo sobre camiones cuyo desenlace ya se conoce.

    Esta es la unica herramienta que consulta la falla registrada. En operacion
    ese dato no existe en el momento de decidir; sirve para responder preguntas
    del tipo "que tan bien funciono" y para dimensionar politicas alternativas.
    """
    flota = _flota()
    if "falla_real" not in flota.columns:
        return {"disponible": False,
                "mensaje": "La flota puntuada no incluye el desenlace registrado."}

    meta = _metadatos()
    costo_fp = meta["costos"]["inspeccion_innecesaria"]
    costo_fn = meta["costos"]["averia_no_detectada"]
    averias = int(flota["falla_real"].sum())

    def _politica(mascara, nombre):
        detectadas = int(((flota["falla_real"] == 1) & mascara).sum())
        falsas = int(((flota["falla_real"] == 0) & mascara).sum())
        perdidas = averias - detectadas
        return {
            "politica": nombre,
            "inspecciones": int(mascara.sum()),
            "averias_detectadas": detectadas,
            "averias_no_detectadas": perdidas,
            "inspecciones_innecesarias": falsas,
            "costo": costo_fp * falsas + costo_fn * perdidas,
            "inspecciones_por_averia_detectada": (
                round(int(mascara.sum()) / detectadas, 1) if detectadas else None
            ),
        }

    resultado = {
        "disponible": True,
        "camiones_evaluados": len(flota),
        "averias_registradas": averias,
        "politica_recomendada": _politica(flota["senalado"] == 1,
                                          "umbral del sistema"),
        "advertencia": "Cifras retrospectivas sobre camiones cuyo desenlace ya se "
                       "conoce. En operacion la decision se toma sin esa informacion.",
    }

    if capacidad is not None:
        capacidad = max(0, min(int(capacidad), len(flota)))
        mascara = pd.Series(False, index=flota.index)
        mascara.iloc[:capacidad] = True
        resultado["politica_por_capacidad"] = _politica(
            mascara, f"inspeccionar los {capacidad} de mayor puntaje")

    return resultado


def panel_operacion(limite: int = 800) -> dict:
    """Datos del tablero de despacho: resumen y camiones ordenados por riesgo.

    No es una herramienta del agente sino la fuente de la interfaz grafica: el
    jefe de taller debe ver la lista al abrir, sin tener que preguntar por ella.
    """
    flota = _flota()
    umbral = _umbral()
    senalados = flota[flota["senalado"] == 1].head(limite)

    filas = []
    for r in senalados.itertuples():
        sensores = _sensores_del_camion(str(r.camion_id))
        motivo = ""
        if sensores and sensores.get("hacia_inspeccionar"):
            motivo = sensores["hacia_inspeccionar"][0]["sensor"]
        filas.append({
            "camion_id": str(r.camion_id),
            "puntaje": round(float(r.riesgo), 6),
            "veces_umbral": round(float(r.riesgo) / umbral, 1) if umbral else None,
            "sensor_principal": motivo,
        })

    total = len(flota)
    senalados_n = int(flota["senalado"].sum())
    return {
        "resumen": resumen_flota(),
        "camiones": filas,
        "mostrados": len(filas),
        "total_senalados": senalados_n,
        "reparto": {
            "senalados": senalados_n,
            "resto": total - senalados_n,
            "total": total,
            "pct_senalados": round(100 * senalados_n / total, 1),
        },
    }


# ---------------------------------------------------------------------------
# Declaracion de las herramientas para la API
# ---------------------------------------------------------------------------

ESQUEMAS = [
    {
        "name": "resumen_flota",
        "description": (
            "Estado general de la flota evaluada: cuantos camiones hay, cuantos "
            "recomienda inspeccionar el sistema, el umbral vigente y el costo de esa "
            "politica frente a no inspeccionar a nadie y a inspeccionar a todos. "
            "Usar para preguntas generales sobre la situacion de la flota."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "consultar_camion",
        "description": (
            "Puntaje de riesgo, decision y sensores que la motivan para un camion "
            "concreto. Usar cuando la pregunta menciona un identificador de camion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "camion_id": {
                    "type": "string",
                    "description": "Identificador del camion, por ejemplo T00347.",
                }
            },
            "required": ["camion_id"],
        },
    },
    {
        "name": "priorizar_inspecciones",
        "description": (
            "Que camiones atender cuando el taller no puede revisar todos los que el "
            "sistema senala. Recibe la capacidad disponible y devuelve los camiones de "
            "mayor puntaje, ademas de cuantos senalados quedarian sin atender."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "capacidad": {
                    "type": "integer",
                    "description": "Numero de inspecciones que el taller puede realizar.",
                }
            },
            "required": ["capacidad"],
        },
    },
    {
        "name": "generar_orden_trabajo",
        "description": (
            "Datos de la orden de inspeccion de un camion: puntaje, cuanto supera el "
            "umbral y que sensores motivan el aviso. Usar cuando se pide una orden de "
            "trabajo, un aviso para el mecanico o instrucciones de revision."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "camion_id": {
                    "type": "string",
                    "description": "Identificador del camion, por ejemplo T00347.",
                }
            },
            "required": ["camion_id"],
        },
    },
    {
        "name": "explicar_sistema",
        "description": (
            "Como funciona el sistema y cuales son sus limites. Temas disponibles: "
            "'general', 'costos', 'umbral', 'sensores', 'limitaciones'. Usar para "
            "preguntas sobre el metodo, la estructura de costos o que puede y no puede "
            "hacer el sistema."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tema": {
                    "type": "string",
                    "enum": ["general", "costos", "umbral", "sensores", "limitaciones"],
                    "description": "Aspecto sobre el que se consulta.",
                }
            },
        },
    },
    {
        "name": "evaluar_desempeno",
        "description": (
            "Resultado retrospectivo del sistema sobre camiones cuyo desenlace ya se "
            "conoce: averias detectadas, no detectadas, inspecciones innecesarias y "
            "costo. Puede evaluar tambien una politica de capacidad limitada. Usar solo "
            "para preguntas sobre que tan bien funciono el sistema, nunca para decidir "
            "sobre un camion concreto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "capacidad": {
                    "type": "integer",
                    "description": "Opcional. Evalua tambien la politica de inspeccionar "
                                   "solo los N camiones de mayor puntaje.",
                }
            },
        },
    },
]

REGISTRO = {
    "resumen_flota": resumen_flota,
    "consultar_camion": consultar_camion,
    "priorizar_inspecciones": priorizar_inspecciones,
    "generar_orden_trabajo": generar_orden_trabajo,
    "explicar_sistema": explicar_sistema,
    "evaluar_desempeno": evaluar_desempeno,
}


def ejecutar(nombre: str, argumentos: dict) -> dict:
    """Invoca una herramienta por nombre y captura cualquier fallo.

    El agente debe recibir siempre una respuesta utilizable: un error capturado y
    descrito es preferible a una excepcion que interrumpa la conversacion.
    """
    if nombre not in REGISTRO:
        return {"error": f"Herramienta desconocida: {nombre}",
                "disponibles": sorted(REGISTRO)}
    try:
        return REGISTRO[nombre](**argumentos)
    except ArtefactoAusente as e:
        return {"error": "artefacto_ausente", "detalle": str(e)}
    except TypeError as e:
        return {"error": "argumentos_invalidos", "detalle": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__, "detalle": str(e)}
