# Convenciones de colaboración — tracking-GPS-VHF

Este documento define cómo trabajamos en este repositorio: nombres de ramas,
formato de commits, y proceso de Pull Request. El objetivo es que cualquiera de
los dos (o una IA asistiendo a cualquiera de los dos) pueda seguir las mismas
reglas sin ambigüedad.

---

## 1. Reglas generales

- **Nunca se commitea directo a `main`.** Todo cambio entra por Pull Request.
- **Todo PR requiere al menos 1 aprobación** antes de mergear (regla configurada
  en el repositorio).
- `main` siempre tiene que quedar en un estado funcional. Si algo está a medio
  terminar, se trabaja en su rama hasta que esté listo para PR.

---

## 2. Ramas

Formato: `tipo/descripcion-corta`

| Tipo | Uso | Ejemplo |
|---|---|---|
| `feature/` | Funcionalidad nueva | `feature/endpoint-telemetria` |
| `fix/` | Corrección de un bug | `fix/parseo-lrrp-altitud` |
| `docs/` | Solo documentación | `docs/api-contrato` |
| `chore/` | Tareas de mantenimiento (deps, config, CI) | `chore/actualizar-dockerfile` |

Reglas:
- Todo en minúsculas, palabras separadas por guiones (`-`), sin espacios ni
  acentos.
- Descripción corta pero clara — alguien que ve el nombre de la rama en la lista
  del repo tiene que entender de qué trata sin abrir nada.
- Una rama = un tema. Si estás resolviendo dos cosas sin relación, van en dos
  ramas separadas.

---

## 3. Commits

Formato: `tipo: descripción en minúscula, sin punto final`

Mismos `tipo` que las ramas (`feature`, `fix`, `docs`, `chore`), más:

| Tipo extra | Uso |
|---|---|
| `refactor` | Cambio interno que no altera comportamiento |
| `test` | Agregar o corregir tests |

Ejemplos:
```
feature: agregar endpoint POST /api/telemetry
fix: corregir parseo de altitud negativa en LRRP
docs: documentar contrato de API en docs/API.md
chore: agregar variables de entorno a .env.example
```

Reglas:
- Un commit = un cambio lógico. Evitar commits gigantes que mezclan varias cosas
  no relacionadas.
- Mensaje en español, tiempo verbal infinitivo ("agregar", "corregir", no
  "agregado" ni "agrego").
- Si el commit cierra o referencia un issue de GitHub, agregarlo al final:
  `fix: corregir timeout en reintento de POST (#12)`

---

## 4. Pull Requests

- **Título:** mismo formato que los commits (`tipo: descripción`).
- **Descripción mínima:** qué cambia y por qué, en 2-3 líneas. No hace falta
  redactar un ensayo — alcanza con que la otra persona entienda el cambio sin
  tener que leer todo el diff primero.
- **Antes de pedir revisión:** confirmar que el servicio propio (`sdr-decoder`,
  `backend` o `frontend`, según corresponda) levanta sin errores de forma local.
- **Revisión:** la otra persona del equipo aprueba o comenta. Si hay comentarios,
  se resuelven en la misma rama antes de mergear — no se abre una rama nueva para
  correcciones menores del mismo PR.
- **Merge:** una vez aprobado, quien abrió el PR lo mergea (no queda pendiente de
  que lo haga la otra persona).

---

## 5. Para asistentes de IA

Si estás usando una IA para ayudarte a preparar un commit, rama, o PR en este
repositorio, indicale que siga las convenciones de este archivo
(`CONTRIBUTING.md`) — los formatos de arriba son el estándar del proyecto, no
sugerencias.