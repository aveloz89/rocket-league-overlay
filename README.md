# Rocket League Live Overlay

Overlay web en tiempo real para Rocket League en PC. Lee la **Stats API oficial**
del juego y muestra **22 stats personales en vivo** del partido en curso + **15
stats agregadas del día** en una página HTML transparente lista para OBS o
ventana flotante.

```
[Rocket League] -- TCP 49123 --> [rl-overlay.exe] -- WebSocket --> [HTML overlay]
                                       |
                                       +--> SQLite (~/.rl-overlay/stats.db)
```

## Cómo usarlo (la forma fácil — un único .exe)

**Setup, una sola vez en cualquier PC Windows:**

1. Descarga la carpeta del proyecto.
2. Doble-click en **`BUILD-EXE.bat`**. Tarda 1-2 min la primera vez:
   - Instala Python si falta.
   - Compila todo en un único `dist\rl-overlay.exe` (~10 MB).

**Para usarlo:**

3. Copia `dist\rl-overlay.exe` donde quieras (escritorio, USB, lo que sea).
4. **Doble-click en `rl-overlay.exe`** y listo. Abre el overlay automáticamente
   en `http://127.0.0.1:8080`.

La primera vez que lo corres, el `.exe` mismo:
- Copia `DefaultStatsAPI.ini` a la carpeta de Rocket League.
- Te avisa que reinicies el juego una vez.
- Detecta tu identidad por la cámara durante el saque (solo la primera partida).

A partir de ahí: doble-click al `.exe` antes de jugar, doble-click a la X cuando
termines.

### Para OBS

Añade un *Browser Source* con URL `http://127.0.0.1:8080`, tamaño 920×900.
El fondo es transparente.

### Probar sin el juego

Doble-click en **`RUN-DEMO.bat`** (corre con datos sintéticos y sirve el overlay
para que veas el layout).

## Alternativa sin compilar a .exe

Si no quieres generar el .exe, usa **`RUN.bat`** directamente — hace lo mismo
pero en cada arranque (en vez de un único binario). Misma experiencia de
doble-click.

## Lo que ves

**Match panel (en vivo):**
- 3 indicadores grandes: SCORE · BOOST · SPEED (con barras)
- **OUTPUT**: Goals · Shots(%) · Saves · Assists · Score/min · Goal partic.%
- **BALL**: Touches · Ball hits · Possession% · Hardest hit · Avg shot pwr · Last touch
- **BOOST**: Avg · Wasted% · Time @ 0 boost%
- **MOVEMENT**: Supersonic% · In air%
- **COMBAT**: Demos given · Demos taken

**Today panel (agregado del día):**
- W–L · Win % · Matches
- **OUTPUT**: Goals · Shots(%) · Saves · Assists · Avg score · Best score
- **ACTIVITY**: Ball hits · Best hit · Demos · Avg boost · Avg supersonic · Win streak

## Estructura

```
BUILD-EXE.bat            compila a dist\rl-overlay.exe (1 vez)
RUN.bat                  alternativa sin compilar
RUN-DEMO.bat             preview con datos sinteticos
app.py                   FastAPI + WebSocket broadcaster + bootstrap del .exe
tcp_client.py            cliente TCP asyncio con reconnect
state.py                 aggregator de la partida (22 stats)
storage.py               SQLite + config en ~/.rl-overlay/
static/                  HTML + CSS + JS del overlay
DefaultStatsAPI.ini      se copia a la carpeta de RL automaticamente
tests/                   26 tests unitarios
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Limitaciones

- **PC only**. La Stats API no existe en consolas.
- **Solo durante la partida**. Entre partidas el socket se cierra; el cliente
  reconecta solo.
- La detección de identidad asume que en el saque inicial la cámara apunta a tu
  coche (default del juego).
