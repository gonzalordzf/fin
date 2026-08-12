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

Todo esto requiere cuentas tuyas (Fly.io, Cloudflare) — no puedo crearlas ni
correr comandos que pidan tus credenciales. Los pasos exactos abajo.

## 1. Backend en Fly.io

Instala `flyctl` si no lo tienes: https://fly.io/docs/flyctl/install/

```bash
flyctl auth login

cd /home/user/fin
flyctl launch --no-deploy   # detecta fly.toml, puede pedirte confirmar/renombrar la app
flyctl volumes create finanzas_data --region qro --size 1   # 1GB de sobra para SQLite

# Genera SESSION_SECRET una sola vez y guárdalo — rotarlo cierra la sesión de todos
python3 -c "import secrets; print(secrets.token_hex(32))"

flyctl secrets set \
  APP_PASSWORD="tu-password-elegido" \
  SESSION_SECRET="<el-hex-generado-arriba>" \
  BBVA_STATEMENT_PASSWORD="ROFG950407"

flyctl deploy
```

`fly.toml` ya apunta `DATABASE_URL` al volumen (`/data/finanzas.db`) y pone
`APP_ENV=production` (cookie de sesión con `https_only=True`).

### Subir la base de datos real

El deploy no trae datos — solo crea las tablas vacías. Tu `backend/finanzas.db`
local (con todo lo ya importado) tiene que subirse una vez al volumen:

```bash
flyctl ssh sftp shell
# dentro de la sesión sftp:
put backend/finanzas.db /data/finanzas.db
```

(o `flyctl ssh console` + `scp`/`curl` si prefieres otra vía — lo único que
importa es que el archivo termine en `/data/finanzas.db` dentro del volumen).

### Verificar

```bash
curl https://<tu-app>.fly.dev/auth/status
# {"authenticated":false} confirma que el backend y el auth gate están vivos
```

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

## 3. Importar estados de cuenta nuevos, ya desplegado

El flujo local (`POST /import/{account}` leyendo `data/imports/<Cuenta>/` del
filesystem) asume acceso al disco de la máquina donde corre el backend. Una
vez desplegado, la forma más simple es seguir corriendo las importaciones
**localmente** (como hasta ahora) contra tu `backend/finanzas.db` local, y
después repetir el `put` de arriba para sincronizar el volumen de Fly.io con
la base actualizada. No hay endpoint de "subir archivo" remoto todavía — si
se vuelve tedioso, es la siguiente pieza a construir (subir el PDF/CSV/XML
por HTTP en vez de por filesystem), pero no se ha construido porque no se ha
pedido.

## Notas

- `APP_PASSWORD`/`SESSION_SECRET`/`BBVA_STATEMENT_PASSWORD` viven solo como
  Fly secrets — nunca en `fly.toml` ni en el repo (`fly.toml` sí se commitea,
  pero solo tiene `DATABASE_URL` y `APP_ENV`, ningún secreto real).
- Dev local (`uvicorn --reload` sin `APP_PASSWORD` seteado) sigue funcionando
  exactamente igual que antes — el gate de login solo se activa cuando
  `APP_PASSWORD` está seteado (ver `backend/app/auth.py`).
- Si rotas `SESSION_SECRET` en producción, todas las sesiones activas
  (incluida la tuya) se invalidan — vuelves a necesitar el password.
