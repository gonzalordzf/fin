# Desplegar a producción

Arquitectura: **Cloudflare Pages** sirve el frontend (React + Vite, estático) y
proxya `/api/*` a un backend FastAPI corriendo en **Fly.io** (el único host
que soporta un volumen persistente para `finanzas.db` — Cloudflare Workers no
puede correr SQLite+SQLAlchemy tal cual).

```
navegador → Cloudflare Pages (frontend estático + Pages Function /api/*)
                    │  (reverse proxy, mismo origen, cookie de sesión intacta)
                    ▼
              Fly.io (FastAPI + SQLite en volumen persistente)
```

El sandbox de Claude Code donde se construyó esto **no tiene salida a
internet general** (solo a registries de paquetes y GitHub — confirmado
directamente, `fly.io` da 403 de política de red) y tampoco hay terminal
local disponible del lado del usuario. Por eso el deploy del backend corre
como **GitHub Actions** (`.github/workflows/deploy-fly.yml`) — los runners de
GitHub sí tienen internet normal. Todo lo que sigue es configuración por
navegador, sin instalar nada ni abrir una terminal.

## 1. Backend en Fly.io (vía GitHub Actions)

### Un token de Fly.io (una sola vez, por navegador)

1. Entra a https://fly.io/user/personal_access_tokens con tu cuenta ya creada.
2. Crea un token nuevo (cualquier nombre, ej. "github-actions-deploy").
3. Cópialo — no se vuelve a mostrar.

### Tres secretos más (los que antes iban por `flyctl secrets set`)

Genera `SESSION_SECRET` una sola vez y guárdalo — rotarlo cierra la sesión de
todos. No hay comando de navegador para esto, pero es una sola línea que
puedes correr en cualquier Python (incluso el intérprete online de
https://www.python.org/shell/ si no tienes terminal):

```python
import secrets; print(secrets.token_hex(32))
```

Necesitas también elegir tu `APP_PASSWORD` (el password de login del
dashboard) y ya tienes `BBVA_STATEMENT_PASSWORD` = `ROFG950407` (tu RFC sin
homoclave, mismo que usas para abrir los PDFs de BBVA).

### Cargar los 4 secretos en GitHub (por navegador)

En el repo → **Settings → Secrets and variables → Actions → New repository
secret**, uno por uno:

| Nombre | Valor |
|---|---|
| `FLY_API_TOKEN` | el token de Fly.io del paso anterior |
| `FLY_APP_PASSWORD` | el password que elegiste para el login del dashboard |
| `FLY_SESSION_SECRET` | el hex de 64 caracteres generado arriba |
| `FLY_BBVA_STATEMENT_PASSWORD` | `ROFG950407` |

### Correr el deploy

El workflow corre solo, en cada push a `main` que toque `backend/**` o
`fly.toml`. Para el primer deploy (todavía en una rama de feature, no en
`main`), dispáralo a mano: **Actions → Deploy backend to Fly.io → Run
workflow**, eligiendo la rama actual.

El workflow: crea la app en Fly.io si no existe, crea el volumen persistente
de 1GB si no existe, sincroniza los 4 secretos, y hace `flyctl deploy`. Si el
nombre de app en `fly.toml` (`finanzas-personales-gonzalo`) ya está tomado
por alguien más (los nombres son globales en Fly.io), el job falla en el paso
de crear la app — en ese caso cambia el valor de `app =` en `fly.toml` por
algo más único y vuelve a correr.

### Verificar

```
https://<tu-app>.fly.dev/auth/status
```
abierto directo en el navegador debe regresar `{"authenticated":false}` —
confirma que el backend y el gate de login están vivos.

## 2. Frontend en Cloudflare Pages

1. En el dashboard de Cloudflare → Pages → **Create a project** → conectar el
   repo de GitHub.
2. Build settings:
   - **Root directory**: `frontend`
   - **Build command**: `npm run build`
   - **Build output directory**: `dist`
3. En **Settings → Environment variables**, agregar:
   - `BACKEND_URL` = `https://<tu-app>.fly.dev` (la URL real de Fly.io del paso 1)

   Esta variable la lee `frontend/functions/api/[[path]].ts`, la Pages
   Function que reenvía todo `/api/*` al backend — así el navegador nunca
   hace una llamada cross-origin y la cookie de sesión funciona igual que en
   dev local.
4. Deploy. Cloudflare construye y publica automáticamente en cada push a la
   rama configurada.

### Verificar

Abrir la URL de Cloudflare Pages → debe aparecer la pantalla de login
(`AuthGate`, ver `frontend/src/AuthGate.tsx`) antes de cualquier dato.

## 3. Subir tu `finanzas.db` real al volumen — TODAVÍA SIN RESOLVER

El deploy de arriba crea las tablas vacías, no trae tus datos. Todas las vías
normales para mover el archivo (`flyctl ssh sftp`, `scp`) requieren `flyctl`
en una terminal, que es justo lo que no tienes. No elegí una alternativa por
mi cuenta porque las tres tocan tus datos financieros reales de formas
distintas — es tu llamada:

- **Consola web de Fly.io** (dashboard → tu app → Console, sin instalar
  nada): abre una shell en el navegador dentro de la máquina. Se puede pegar
  el archivo como base64 en pedazos y reconstruirlo con `base64 -d`, pero es
  tedioso a mano para un archivo de varios cientos de KB/unos MB.
- **Subirlo temporalmente vía GitHub Actions** (artifact o release asset):
  yo escribiría un workflow que reciba el archivo y lo empuje al volumen por
  `flyctl ssh sftp`. Funciona, pero significa que tu base de datos real toca
  la infraestructura de GitHub aunque sea de forma efímera — justo lo que la
  regla del proyecto ("los archivos financieros reales nunca se commitean")
  intenta evitar, aunque técnicamente no sería un commit de git.
- **Construir un endpoint de subida autenticado** en el propio backend (ej.
  `POST /admin/restore-db`, protegido por el mismo gate de sesión que ya
  existe) — lo subes por HTTPS una vez que el backend ya está desplegado y
  con login, sin terminal ni GitHub de por medio. Es la vía más limpia a
  largo plazo (también resolvería el pendiente de importar estados nuevos ya
  desplegado, ver abajo) pero es una pieza nueva de código, no algo que ya
  exista.

Dime cuál prefieres y lo construyo/ejecuto.

## 4. Importar estados de cuenta nuevos, ya desplegado

Mismo problema que el punto 3: el flujo local (`POST /import/{account}`
leyendo `data/imports/<Cuenta>/` del filesystem) asume acceso al disco de la
máquina donde corre el backend. Si se construye el endpoint de subida
autenticado del punto 3, resuelve ambos pendientes a la vez.

## Notas

- `APP_PASSWORD`/`SESSION_SECRET`/`BBVA_STATEMENT_PASSWORD` viven solo como
  Fly secrets (sincronizados desde GitHub Actions secrets) — nunca en
  `fly.toml` ni en el repo (`fly.toml` sí se commitea, pero solo tiene
  `DATABASE_URL` y `APP_ENV`, ningún secreto real).
- Dev local (`uvicorn --reload` sin `APP_PASSWORD` seteado) sigue funcionando
  exactamente igual que antes — el gate de login solo se activa cuando
  `APP_PASSWORD` está seteado (ver `backend/app/auth.py`).
- Si rotas `SESSION_SECRET` en producción, todas las sesiones activas
  (incluida la tuya) se invalidan — vuelves a necesitar el password.
