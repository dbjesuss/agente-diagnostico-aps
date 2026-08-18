# -*- coding: utf-8 -*-
"""Genera models/contribuciones_por_sensor.csv a partir de los artefactos ya guardados.

El cuaderno de modelado deja el modelo y el explicador serializados, de modo que
las contribuciones de cada sensor a cada camion pueden recalcularse sin volver a
entrenar. Esto tarda un par de minutos en lugar de los veinte que costaria
reejecutar el cuaderno completo.

El archivo resultante permite que la orden de trabajo de cada camion cite los
sensores que realmente motivan *ese* aviso. Sin el, la capa de priorizacion se
apoya en las tres lecturas mas influyentes del conjunto, que son las mismas para
todos los vehiculos y hacen que todas las ordenes digan lo mismo.

    python src/generar_contribuciones.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import shap

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "data" / "raw"
DIR_MODELOS = RAIZ / "models"


def cargar_aps(ruta: Path) -> pd.DataFrame:
    """Carga un archivo del conjunto APS localizando la fila de encabezados."""
    with open(ruta, encoding="utf-8") as f:
        for i, linea in enumerate(f):
            if linea.startswith("class,"):
                return pd.read_csv(ruta, skiprows=i, na_values="na")
    raise ValueError(f"No se encontro la fila de encabezados en {ruta}")


def main() -> int:
    ruta_modelo = DIR_MODELOS / "modelo_aps.joblib"
    ruta_prueba = DIR_DATOS / "aps_failure_test_set.csv"

    for ruta in (ruta_modelo, ruta_prueba):
        if not ruta.exists():
            print(f"Falta {ruta}.")
            print("Ejecute primero el cuaderno 02_modelado.ipynb y verifique que los")
            print("archivos de datos esten en data/raw.")
            return 1

    print("Cargando el modelo...")
    tuberia = joblib.load(ruta_modelo)
    imputador = tuberia.named_steps["imputador"]
    modelo = tuberia.named_steps["modelo"]

    print("Cargando los camiones de prueba...")
    prueba = cargar_aps(ruta_prueba)
    variables = [c for c in prueba.columns if c != "class"]
    X = imputador.transform(prueba[variables])

    print(f"Calculando contribuciones para {len(X):,} camiones "
          f"y {X.shape[1]} lecturas...")
    explicador = shap.TreeExplainer(modelo)
    valores = explicador.shap_values(X)

    # Las columnas de una misma familia son rangos de un mismo sensor: se suman,
    # porque es al nivel del sensor al que el taller puede actuar.
    contrib = pd.DataFrame(np.asarray(valores, dtype=float), columns=X.columns)
    contrib.columns = [c.rsplit("_", 1)[0] for c in contrib.columns]
    por_sensor = contrib.T.groupby(level=0).sum().T
    por_sensor.insert(0, "camion_id", [f"T{k:05d}" for k in range(len(por_sensor))])

    destino = DIR_MODELOS / "contribuciones_por_sensor.csv"
    por_sensor.round(4).to_csv(destino, index=False)

    tam = destino.stat().st_size / 1e6
    print(f"\nGuardado: models/{destino.name}  ({tam:.2f} MB)")
    print(f"  {len(por_sensor):,} camiones x {por_sensor.shape[1] - 1} sensores")

    # Comprobacion: las contribuciones deben reconstruir el puntaje del modelo
    suma = por_sensor.drop(columns="camion_id").sum(axis=1) + explicador.expected_value
    esperado = modelo.predict(X, output_margin=True)
    desvio = float(np.max(np.abs(suma.to_numpy() - esperado)))
    print(f"  desvio maximo frente al puntaje del modelo: {desvio:.2e}")
    print("  verificado." if desvio < 1e-3 else "  ADVERTENCIA: no reconstruye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
