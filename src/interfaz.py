# -*- coding: utf-8 -*-
"""Tablero de despacho del taller, con el agente integrado.

Levanta un servidor en la maquina de quien lo ejecuta. La pantalla muestra a la
izquierda los camiones que hay que revisar hoy, ordenados por riesgo, y a la
derecha el asistente para preguntar por cualquiera de ellos.

    python src/interfaz.py

Luego abrir http://localhost:8000 en el navegador.

Usa unicamente la biblioteca estandar: no requiere instalar nada mas alla de lo
que ya necesita el agente.

SOBRE LA CLAVE DE API. El servidor lee ANTHROPIC_API_KEY del archivo .env de
quien lo ejecuta y nunca la pide por pantalla. Esta pensado para uso local: si se
publicara en internet, quien lo visite estaria consumiendo la clave del que lo
alojo. Pedirle su clave al visitante por un formulario tampoco seria aceptable,
porque esa credencial viajaria a un servidor ajeno.
"""

from __future__ import annotations

import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import herramientas  # noqa: E402

PUERTO = 8000

PAGINA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Despacho de inspecciones &mdash; APS</title>
<style>
  :root {
    --zinc:    #dfe3e6;
    --tablero: #f2f4f5;
    --tinta:   #10161c;
    --linea:   #c3ccd3;
    --suave:   #5c6a75;
    --tenue:   #93a1ad;
    --ambar:   #b8720b;
    --alarma:  #99291c;
    --dato: ui-monospace, "SF Mono", "Cascadia Mono", Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; background: var(--zinc); color: var(--tinta);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    display: grid; grid-template-rows: auto 1fr;
    font-variant-numeric: tabular-nums;
  }

  /* ---- cabecera ---- */
  header {
    background: var(--tinta); color: var(--tablero);
    padding: 13px 22px; display: flex; align-items: center;
    gap: 14px; flex-wrap: wrap;
  }
  .marca { display: flex; align-items: center; gap: 11px; }
  .marca svg { display: block; }
  header h1 {
    margin: 0; font-size: 12px; font-weight: 600; line-height: 1.35;
    text-transform: uppercase; letter-spacing: 0.16em;
  }
  header h1 small {
    display: block; font-size: 10px; font-weight: 400; letter-spacing: 0.1em;
    color: var(--tenue); text-transform: none;
  }
  .cifras { display: flex; gap: 26px; margin-left: auto; flex-wrap: wrap; }
  .cifra { display: flex; flex-direction: column; }
  .cifra b { font: 600 17px/1 var(--dato); letter-spacing: -0.02em;
             color: #c6cfd6; }
  .cifra span {
    font-size: 9px; text-transform: uppercase; letter-spacing: 0.13em;
    color: var(--tenue); margin-top: 4px;
  }
  /* El ahorro es el titular del proyecto: debe dominar sobre las otras dos */
  .cifra.titular b { font-size: 30px; color: #fff; letter-spacing: -0.03em; }
  .cifra.titular span { color: #b9c4cd; }
  .cifras { align-items: baseline; }

  main { display: grid; grid-template-columns: minmax(370px, 5fr) 6fr; min-height: 0; }

  /* ---- izquierda: tablero ---- */
  #tablero { background: var(--tablero); border-right: 1px solid var(--linea);
             display: grid; grid-template-rows: auto auto 1fr; min-height: 0; }
  .rotulo {
    padding: 10px 20px 9px; border-bottom: 1px solid var(--linea);
    display: flex; align-items: center; gap: 10px;
  }
  .rotulo h2 {
    margin: 0; font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.13em; color: var(--suave); white-space: nowrap;
  }
  #buscar {
    margin-left: auto; font: 12px var(--dato); padding: 4px 9px; width: 118px;
    border: 1px solid var(--linea); border-radius: 3px; background: #fff;
    color: var(--tinta);
  }
  #buscar:focus-visible { outline: 2px solid var(--ambar); outline-offset: -1px; }

  /* Reparto: donde caen los senalados dentro de la flota completa */
  .reparto { padding: 11px 20px 12px; border-bottom: 1px solid var(--linea); }
  .cinta { display: flex; height: 7px; border-radius: 2px; overflow: hidden;
           background: #d3dade; }
  .cinta i { display: block; height: 100%; }
  .cinta .marcados { background: var(--ambar); }
  .pie {
    display: flex; justify-content: space-between; margin-top: 6px;
    font-size: 10px; color: var(--suave); text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .pie b { font-family: var(--dato); font-weight: 600; color: var(--tinta); }
  .aclaracion {
    margin: 7px 0 0; font-size: 10.5px; line-height: 1.4; color: var(--suave);
  }
  .aclaracion:empty { display: none; }
  .lectura sup { color: var(--ambar); font-size: 9px; }

  #lista { overflow-y: auto; }

  /* Cada camion es una boleta: matricula, manometro, lectura */
  .boleta {
    display: grid; grid-template-columns: 72px 1fr 58px;
    align-items: center; gap: 13px;
    padding: 9px 20px; border-bottom: 1px solid #e2e7ea;
    cursor: pointer; background: none; border-left: 3px solid transparent;
    width: 100%; text-align: left; font: inherit; color: inherit;
  }
  .boleta:hover, .boleta:focus-visible {
    background: #e8ecee; border-left-color: var(--ambar); outline: none;
  }
  .matricula { font: 600 13px var(--dato); letter-spacing: 0.03em; }
  .puesto { font: 9px var(--dato); color: var(--tenue); display: block;
            letter-spacing: 0.05em; }
  .manometro { position: relative; height: 14px; }
  /* Carátula: las marcas verticales hacen que se lea como un instrumento y no
     como una barra de progreso. */
  .escala {
    position: absolute; inset: 5px 0 auto 0; height: 4px;
    background: #d3dade; border-radius: 2px;
  }
  .caratula {
    position: absolute; inset: 0 0 auto 0; height: 14px; pointer-events: none;
    background-image: repeating-linear-gradient(to right,
      var(--tenue) 0 1px, transparent 1px 10%);
    opacity: .5;
  }
  .aguja {
    position: absolute; top: 5px; height: 4px; left: 0;
    background: var(--ambar); border-radius: 2px;
    transform-origin: left center;
  }
  /* La aguja entra desde cero: en el primer segundo la pantalla cobra vida */
  @media (prefers-reduced-motion: no-preference) {
    .aguja { animation: subir .55s cubic-bezier(.22,.9,.3,1) both; }
    @keyframes subir { from { transform: scaleX(0); } to { transform: scaleX(1); } }
  }
  .boleta.critico .aguja { background: var(--alarma); }
  .marca-umbral { position: absolute; top: 1px; width: 1px; height: 12px;
                  background: var(--suave); left: 0; }
  .lectura { font: 12px var(--dato); color: var(--suave); text-align: right; }
  .lectura b { color: var(--tinta); font-weight: 600; }
  .sensor { font: 10px var(--dato); color: var(--suave); text-transform: uppercase;
            letter-spacing: 0.09em; }
  /* Bandas de urgencia: agrupan la lista en los tramos con que el taller decide */
  .banda {
    position: sticky; top: 0; z-index: 2;
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 7px 20px 6px; background: #e7ebed;
    border-top: 1px solid var(--linea); border-bottom: 1px solid var(--linea);
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.13em;
    color: var(--suave);
  }
  .banda em { font-style: normal; display: flex; align-items: center; gap: 7px; }
  .banda em::before {
    content: ""; width: 7px; height: 7px; border-radius: 1px;
    background: var(--ambar);
  }
  .banda.critica em::before { background: var(--alarma); }
  .banda.leve em::before { background: var(--tenue); }
  .banda b { font-family: var(--dato); font-weight: 600; color: var(--tinta); }

  /* Regla de planilla: una linea marcada cada cinco filas da ritmo vertical */
  .boleta:nth-child(5n) { border-bottom-color: var(--linea); }

  .vacio { padding: 26px 20px; color: var(--suave); font-size: 14px; }
  .vacio button {
    display: block; margin-top: 10px; font: inherit; font-size: 13px;
    background: #fff; border: 1px solid var(--linea); border-radius: 3px;
    padding: 7px 12px; cursor: pointer; color: var(--tinta);
  }
  .vacio button:hover { border-color: var(--ambar); }

  /* ---- derecha: asistente ---- */
  #panel { display: grid; grid-template-rows: 1fr auto; min-height: 0;
           background: var(--tablero); }
  #hilo { overflow-y: auto; padding: 22px 24px; }

  /* Tarjeta de apertura: el argumento economico, visible sin preguntar */
  #apertura {
    background: #fff; border: 1px solid var(--linea); border-radius: 3px;
    padding: 18px 20px 16px; max-width: 560px; margin-bottom: 22px;
  }
  #apertura h3 {
    margin: 0 0 3px; font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.13em; color: var(--suave);
  }
  #apertura p { margin: 0 0 15px; font-size: 13px; color: var(--suave); }
  .politica { margin-bottom: 11px; }
  .politica .fila {
    display: flex; justify-content: space-between; font-size: 12px;
    margin-bottom: 3px;
  }
  .politica .fila em { font-style: normal; }
  .politica .fila b { font-family: var(--dato); font-weight: 600; }
  .politica .barra { height: 9px; background: #d3dade; border-radius: 2px; }
  .politica .barra i { display: block; height: 100%; border-radius: 2px;
                       background: var(--tenue); }
  .politica.gana .barra i { background: var(--ambar); }
  .politica.gana .fila b { color: var(--ambar); }
  #apertura footer {
    border: none; padding: 12px 0 0; margin-top: 13px;
    border-top: 1px solid var(--linea); font-size: 12px; color: var(--suave);
  }

  .turno { margin-bottom: 20px; max-width: 640px; }
  .quien { font-size: 9px; text-transform: uppercase; letter-spacing: 0.13em;
           color: var(--suave); margin-bottom: 5px; }
  .dicho { background: #fff; border: 1px solid var(--linea); border-radius: 3px;
           padding: 12px 15px; white-space: pre-wrap; }
  .usuario .dicho { background: #e5eaed; border-color: #d2dade; }
  /* Filete solo en el asistente: da ritmo a la conversacion sin anadir color */
  .turno:not(.usuario):not(.error) .dicho { border-left: 3px solid var(--ambar); }
  .error .dicho { border-left: 3px solid var(--alarma); color: var(--alarma); }
  .consultado { margin-top: 6px; font: 11px var(--dato); color: var(--suave); }
  .consultado i { font-style: normal; border-bottom: 1px dotted var(--linea);
                  margin-right: 9px; }
  .pensando .dicho { color: var(--suave); font-style: italic; }

  #panel > footer { border-top: 1px solid var(--linea); padding: 12px 24px 16px; }
  .atajos { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 9px; }
  .atajos button {
    font: inherit; font-size: 12px; color: var(--suave); background: #fff;
    border: 1px solid var(--linea); border-radius: 14px; padding: 4px 11px;
    cursor: pointer;
  }
  .atajos button:hover { border-color: var(--ambar); color: var(--ambar); }
  #barra { display: flex; gap: 8px; }
  textarea {
    flex: 1; resize: none; font: inherit; padding: 10px 12px;
    border: 1px solid var(--linea); border-radius: 3px; background: #fff;
    min-height: 42px;
  }
  textarea:focus-visible { outline: 2px solid var(--ambar); outline-offset: -1px; }
  #enviar {
    font: inherit; font-weight: 600; padding: 0 20px; border: none;
    border-radius: 3px; background: var(--tinta); color: var(--tablero);
    cursor: pointer;
  }
  #enviar:disabled { opacity: .4; cursor: default; }
  #enviar:focus-visible { outline: 2px solid var(--ambar); outline-offset: 2px; }

  @media (max-width: 900px) {
    main { grid-template-columns: 1fr; grid-template-rows: 250px 1fr; }
    #tablero { border-right: none; border-bottom: 1px solid var(--linea); }
    .cifras { gap: 16px; margin-left: 0; }
    .cifra b { font-size: 17px; }
  }
  @media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
</style>
</head>
<body>
<header>
  <span class="marca">
    <!-- Camion de carga con manometro por rueda: prioriza por presion de aire -->
    <svg width="46" height="24" viewBox="0 0 62 32" aria-hidden="true">
      <g fill="none" stroke="#f2f4f5" stroke-width="2" stroke-linejoin="round">
        <path d="M2 6h30v16H2z"/>
        <path d="M34 11h9l6 6v5H34z"/>
        <path d="M2 22h47"/>
      </g>
      <circle cx="13" cy="25" r="5" fill="none" stroke="#f2f4f5" stroke-width="2"/>
      <circle cx="41" cy="25" r="5" fill="none" stroke="#b8720b" stroke-width="2"/>
      <path d="M41 25l3-3" stroke="#b8720b" stroke-width="2" stroke-linecap="round"/>
    </svg>
    <h1>Despacho de inspecciones<small>Sistema de aire comprimido</small></h1>
  </span>
  <div class="cifras" id="cifras"></div>
</header>

<main>
  <section id="tablero">
    <div class="rotulo">
      <h2>Camiones a revisar</h2>
      <input id="buscar" type="search" placeholder="Buscar T00000" aria-label="Buscar camion">
    </div>
    <div class="reparto" id="reparto"></div>
    <div id="lista"><p class="vacio">Cargando el tablero...</p></div>
  </section>

  <section id="panel">
    <div id="hilo"></div>
    <footer>
      <div class="atajos" id="atajos">
        <button>Cuantos camiones hay que revisar hoy?</button>
        <button>Solo alcanzo para 200 revisiones, a cuales priorizo?</button>
        <button>Que resultados dio el sistema?</button>
        <button>Por que se revisan camiones que estan bien?</button>
      </div>
      <div id="barra">
        <textarea id="texto" rows="1" placeholder="Pregunte por un camion o por la operacion del dia"></textarea>
        <button id="enviar">Enviar</button>
      </div>
    </footer>
  </section>
</main>

<script>
const hilo = document.getElementById('hilo');
const caja = document.getElementById('texto');
const boton = document.getElementById('enviar');
const buscar = document.getElementById('buscar');
let CAMIONES = [];

const num = n => n.toLocaleString('es-CO');

/* ---- tarjeta de apertura: el argumento economico, sin tener que preguntarlo ---- */
function pintarApertura(r) {
  const politicas = [
    ['No inspeccionar a nadie', r.costo_no_inspeccionar_a_nadie, false],
    ['Inspeccionar toda la flota', r.costo_inspeccionar_a_todos, false],
    ['Priorizar con el sistema', r.costo_politica_recomendada, true],
  ];
  const tope = Math.max.apply(null, politicas.map(p => p[1]));
  const card = document.createElement('div');
  card.id = 'apertura';
  card.innerHTML =
    '<h3>Costo de la operacion</h3>' +
    '<p>Que cuesta cada forma de decidir a quien se revisa.</p>' +
    politicas.map(function (p) {
      return '<div class="politica' + (p[2] ? ' gana' : '') + '">' +
        '<div class="fila"><em>' + p[0] + '</em><b>' + num(p[1]) + '</b></div>' +
        '<div class="barra"><i style="width:' + (p[1] / tope * 100).toFixed(1) +
        '%"></i></div></div>';
    }).join('') +
    '<footer>Una averia en ruta cuesta 50 veces una revision innecesaria. ' +
    'Por eso conviene revisar de mas.</footer>';
  hilo.appendChild(card);
}

function quitarApertura() {
  const c = document.getElementById('apertura');
  if (c) c.remove();
}

/* ---- tablero ---- */
function dibujarLista(camiones) {
  const lista = document.getElementById('lista');
  if (!camiones.length) {
    const t = buscar.value.trim().toUpperCase();
    lista.innerHTML = '<div class="vacio">' +
      (t ? 'Ningun camion senalado coincide con <b>' + t + '</b>.' +
           '<button id="preguntar-por">Preguntar al asistente por ' + t + '</button>'
         : 'Ningun camion supera el umbral.') + '</div>';
    const b = document.getElementById('preguntar-por');
    if (b) b.onclick = () => enviar('Dame el estado del camion ' + t);
    return;
  }

  /* La aguja se reparte entre el menor y el mayor: en escala absoluta todas
     quedarian pegadas al tope y no se distinguiria ninguna. */
  const veces = CAMIONES.map(c => Math.max(c.veces_umbral || 1, 1));
  const tope = Math.max.apply(null, veces);
  const piso = Math.min.apply(null, veces);
  const rango = Math.log10(tope) - Math.log10(piso) || 1;
  const corte = veces[Math.floor(veces.length * 0.1)] || tope;

  /* Muchos camiones comparten el mismo multiplo porque su puntaje esta en el
     tope de la escala. Entre ellos la aguja no puede distinguir nada, asi que
     dentro de ese bloque se reparte por posicion en la flota, que si los
     ordena. Fuera del bloque la lectura sigue siendo el multiplo. */
  const nSaturados = veces.filter(v => v >= tope).length;
  const haySaturados = nSaturados > 1;

  function largoAguja(c) {
    const v = Math.max(c.veces_umbral || 1, 1);
    if (haySaturados && v >= tope) {
      /* Reparte el tramo superior de la escala (de 70% a 100%) entre los
         saturados segun su puesto: el n.o 1 marca el maximo. */
      const avance = (c.puesto - 1) / Math.max(nSaturados - 1, 1);
      return 100 - avance * 30;
    }
    const base = 8 + (Math.log10(v) - Math.log10(piso)) / rango * 92;
    return haySaturados ? Math.min(base, 70) : base;
  }

  /* Tres tramos de urgencia. Los cortes salen de la propia lista: el decil
     superior es critico y el cuartil siguiente, alto. Sin bandas las 745
     boletas se leen como una masa uniforme y nada destaca. */
  const corteAlto = veces[Math.floor(veces.length * 0.35)] || piso;

  function tramo(c) {
    const v = Math.max(c.veces_umbral || 1, 1);
    if (v >= corte) return 0;
    if (v >= corteAlto) return 1;
    return 2;
  }

  const bandas = [
    { clase: 'critica', nombre: 'Critico' },
    { clase: '',        nombre: 'Alto' },
    { clase: 'leve',    nombre: 'Moderado' },
  ];

  const grupos = [[], [], []];
  camiones.forEach(function (c) { grupos[tramo(c)].push(c); });

  let html = '', orden = 0;
  grupos.forEach(function (grupo, k) {
    if (!grupo.length) return;
    html += '<div class="banda ' + bandas[k].clase + '"><em>' + bandas[k].nombre +
            '</em><b>' + num(grupo.length) + '</b></div>';
    html += grupo.map(function (c) {
      const largo = largoAguja(c);
      /* Escalonar la entrada de las agujas, con tope para que no se alargue */
      const retraso = Math.min(orden++, 24) * 22;
      return '<button class="boleta' + (k === 0 ? ' critico' : '') +
        '" data-id="' + c.camion_id + '">' +
        '<span><span class="matricula">' + c.camion_id + '</span>' +
        '<span class="puesto">n.&deg; ' + c.puesto + '</span></span>' +
        '<span class="manometro"><span class="escala"></span>' +
        '<span class="caratula"></span>' +
        '<span class="aguja" style="width:' + largo.toFixed(1) +
        '%;animation-delay:' + retraso + 'ms"></span>' +
        '<span class="marca-umbral"></span></span>' +
        '<span class="lectura"><b>' + c.veces_umbral +
        (haySaturados && Math.max(c.veces_umbral || 1, 1) >= tope
          ? '&times;<sup>+</sup>' : '&times;') + '</b><br>' +
        '<span class="sensor">' + (c.sensor_principal || '&mdash;') + '</span></span>' +
        '</button>';
    }).join('');
  });
  lista.innerHTML = html;

  lista.querySelectorAll('.boleta').forEach(function (b) {
    b.onclick = function () {
      enviar('Por que hay que revisar el camion ' + b.dataset.id +
             '? Dame la orden de trabajo.');
    };
  });
}

async function cargarTablero() {
  const lista = document.getElementById('lista');
  try {
    const d = await (await fetch('/api/panel')).json();
    if (d.error) { lista.innerHTML = '<p class="vacio">' + d.error + '</p>'; return; }

    const r = d.resumen;
    document.getElementById('cifras').innerHTML = [
      ['<b>' + num(r.camiones_senalados) + '</b>', 'a revisar', ''],
      ['<b>' + num(r.camiones_evaluados) + '</b>', 'en la flota', ''],
      ['<b>' + r.reduccion_vs_correctivo_pct + '%</b>', 'menos costo', ' titular'],
    ].map(c => '<div class="cifra' + c[2] + '">' + c[0] +
               '<span>' + c[1] + '</span></div>').join('');

    const rp = d.reparto;
    document.getElementById('reparto').innerHTML =
      '<div class="cinta"><i class="marcados" style="width:' + rp.pct_senalados +
      '%"></i></div><div class="pie"><span><b>' + num(rp.senalados) +
      '</b> senalados &middot; ' + rp.pct_senalados + '% de la flota</span>' +
      '<span><b>' + num(rp.resto) + '</b> sin senalar</span></div>' +
      '<p class="aclaracion" id="aclaracion"></p>';

    CAMIONES = d.camiones.map((c, i) => Object.assign({ puesto: i + 1 }, c));
    dibujarLista(CAMIONES);

    const vs = CAMIONES.map(c => Math.max(c.veces_umbral || 1, 1));
    const nSat = vs.filter(v => v >= Math.max.apply(null, vs)).length;
    if (nSat > 1) {
      document.getElementById('aclaracion').textContent =
        num(nSat) + ' camiones tienen el puntaje en el tope de la escala y ' +
        'comparten multiplo (marcado +). Entre ellos ordena el puesto en la flota.';
    }
    pintarApertura(r);
  } catch (e) {
    lista.innerHTML = '<p class="vacio">No se pudo leer el tablero: ' + e + '</p>';
  }
}

buscar.oninput = function () {
  const t = buscar.value.trim().toUpperCase();
  dibujarLista(t ? CAMIONES.filter(c => c.camion_id.indexOf(t) !== -1) : CAMIONES);
};

/* ---- conversacion ---- */
/* El asistente responde en markdown ligero. Se convierte solo lo imprescindible
   escapando antes todo el texto: interpretar HTML crudo de un modelo seria una
   via de inyeccion. */
function formatear(texto) {
  return texto
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/^(\s*)[-*]\s+/gm, '$1\u2022 ');
}

function pintar(quien, texto, consultado, esError, clase) {
  const d = document.createElement('div');
  d.className = 'turno ' + (clase || '') + (quien === 'Usted' ? ' usuario' : '') +
                (esError ? ' error' : '');
  d.innerHTML = '<div class="quien">' + quien + '</div>';
  const b = document.createElement('div');
  b.className = 'dicho';
  if (quien === 'Asistente' && !clase) b.innerHTML = formatear(texto);
  else b.textContent = texto;
  d.appendChild(b);
  if (consultado && consultado.length) {
    const c = document.createElement('div');
    c.className = 'consultado';
    c.innerHTML = 'consulto ' + consultado.map(x => '<i>' + x + '</i>').join('');
    d.appendChild(c);
  }
  hilo.appendChild(d);
  d.scrollIntoView({ block: 'end' });
  return d;
}

async function enviar(mensaje) {
  if (!mensaje.trim() || boton.disabled) return;
  quitarApertura();
  pintar('Usted', mensaje);
  caja.value = ''; boton.disabled = true; boton.textContent = 'Consultando';
  const espera = pintar('Asistente', 'Consultando el sistema...', null, false, 'pensando');
  try {
    const res = await fetch('/api/preguntar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mensaje: mensaje })
    });
    const d = await res.json();
    espera.remove();
    if (d.error) pintar('Sistema', d.error, null, true);
    else pintar('Asistente', d.respuesta, d.herramientas);
  } catch (e) {
    espera.remove();
    pintar('Sistema', 'No se pudo contactar el servidor: ' + e, null, true);
  }
  boton.disabled = false; boton.textContent = 'Enviar';
  caja.focus();
}

boton.onclick = function () { enviar(caja.value); };
caja.onkeydown = function (e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviar(caja.value); }
};
document.querySelectorAll('#atajos button').forEach(function (b) {
  b.onclick = function () { enviar(b.textContent); };
});

cargarTablero();
caja.focus();
</script>
</body>
</html>
"""


class Manejador(BaseHTTPRequestHandler):
    agente = None
    error_arranque = None

    def _responder(self, codigo, cuerpo, tipo="application/json; charset=utf-8"):
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _json(self, objeto, codigo=200):
        self._responder(codigo, json.dumps(objeto, ensure_ascii=False, default=str))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._responder(200, PAGINA, "text/html; charset=utf-8")
        elif self.path == "/api/panel":
            try:
                self._json(herramientas.panel_operacion())
            except herramientas.ArtefactoAusente as e:
                self._json({"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"No se pudo leer el tablero: {e}"})
        else:
            self._json({"error": "No encontrado"}, 404)

    def _asegurar_agente(self):
        """Reintenta crear el agente si al arrancar faltaba la clave.

        Asi, quien corrige el archivo .env solo tiene que volver a preguntar en
        lugar de reiniciar el servidor.
        """
        if Manejador.agente is not None:
            return None
        from agente import Agente
        try:
            Manejador.agente = Agente()
            Manejador.error_arranque = None
            return None
        except RuntimeError as e:
            Manejador.error_arranque = str(e)
            return str(e)

    def do_POST(self):
        if self.path != "/api/preguntar":
            self._json({"error": "No encontrado"}, 404)
            return
        fallo = self._asegurar_agente()
        if fallo:
            self._json({"error": fallo})
            return
        try:
            largo = int(self.headers.get("Content-Length", 0))
            mensaje = str(json.loads(self.rfile.read(largo) or b"{}")
                          .get("mensaje", "")).strip()
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "Peticion mal formada"}, 400)
            return
        if not mensaje:
            self._json({"error": "Escriba una consulta."})
            return

        antes = len(self.agente.registro_llamadas)
        try:
            respuesta = self.agente.preguntar(mensaje)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"Error al consultar el modelo: {e}"})
            return
        usadas = [ll["herramienta"] for ll in self.agente.registro_llamadas[antes:]]
        self._json({"respuesta": respuesta, "herramientas": usadas})

    def log_message(self, formato, *args):
        """Silencia el registro por peticion; solo interesan los errores."""
        return


def main():
    from agente import Agente

    try:
        Manejador.agente = Agente()
    except RuntimeError as e:
        Manejador.error_arranque = str(e)
        print(f"AVISO: {e}")
        print("El tablero abrira, pero el asistente no podra responder.\n")

    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    url = f"http://localhost:{PUERTO}"
    print(f"Tablero disponible en {url}")
    print("Ctrl+C para detener.\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        servidor.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
