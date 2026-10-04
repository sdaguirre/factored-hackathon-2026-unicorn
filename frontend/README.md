# chat-frontend

Interfaz de chat para el `backend`: documento, preguntas de seguridad, conversación en español y portugués,
oferta indicativa y aviso de derivación a un asesor. HTML, CSS y JavaScript puros: **sin dependencias ni paso de
compilación**, así que se puede servir con nginx, subir a Cloudflare Pages o copiar junto a otra aplicación.

> Prototipo con datos sintéticos. La interfaz lo indica en el encabezado y en el pie.

## Arranque

Desde la raíz del repositorio (levanta backend e interfaz; el backend lee `backend/.env`):

```bash
docker compose up --build
```

Abra `http://localhost:8080`. El puerto del backend no se publica: el navegador solo habla con el nginx de la
interfaz, que reenvía `/v1` al backend y agrega ahí la clave de integración (`CHAT_API_KEY`), de modo que **la clave
nunca llega al navegador** y no hace falta CORS.

Sin Docker, para desarrollo: sirva esta carpeta con cualquier servidor estático y ponga en `config.js`
`apiBase: "http://127.0.0.1:8000"`; el backend debe permitir ese origen con `CHAT_CORS_ORIGINS`.

## Pantallas

1. **Documento**: valida el formato antes de llamar a la API (4 a 32 letras, números, puntos o guiones).
2. **Preguntas de seguridad**: tres preguntas de opción única; "Verificar" se activa al responder todas. Si fallan,
   muestra los intentos restantes y las preguntas nuevas; al bloquearse vuelve al inicio con el aviso.
3. **Chat**: mensajes con indicador de escritura, botones de respuestas sugeridas, globo verde "Oferta indicativa"
   cuando la API marca `proactive_offer`, tarjeta con el número de seguimiento cuando hay derivación.
4. **Fin**: al terminar la conversación (cierra la sesión en el servidor) o si la sesión expira.

Idioma: se toma del navegador (es o pt) y se cambia con el selector; el idioma elegido viaja en cada mensaje.

## Seguridad

- Todo texto que llega de la API se inserta con `textContent`, nunca como HTML. Probado enviando
  `<img onerror=…>` y `<script>`: se muestra como texto y no se ejecuta nada.
- El token de sesión vive solo en memoria de la página (no en `localStorage`): recargar la página cierra la sesión.
- nginx añade `Content-Security-Policy` estricta (sin scripts ni estilos en línea; solo mismo origen),
  `X-Content-Type-Options` y `Referrer-Policy: no-referrer`. El contenedor corre sin privilegios y con el sistema de
  archivos en solo lectura.
- No se muestran los códigos internos de error al cliente, salvo el `trace_id` en errores inesperados (para soporte).

## Despliegue en un hosting estático (p. ej. Cloudflare Pages)

Suba el contenido de esta carpeta (menos `nginx/`, `Dockerfile` y este README), ponga en `config.js` la URL pública
del backend en `apiBase` y configure `CHAT_CORS_ORIGINS` en el backend con el origen de la página. **Una `apiKey` en
`config.js` sería visible para cualquiera**: si el backend exige clave de integración, use un proxy que la agregue en
el servidor (como hace el nginx de este contenedor) en vez de ponerla en el navegador.

## Qué se probó y qué no

Probado a mano en el navegador integrado, con el stack en Docker y el modelo real: validación del documento, intento
fallido con preguntas nuevas, autenticación, sugerencias, conversación en español y en portugués, oferta indicativa,
incidente con derivación y tarjeta de seguimiento (sin oferta), bloqueo por intentos (con un documento inexistente, sin
revelar qué documentos existen), sesión inválida tras reiniciar el backend, cierre y nueva conversación, inyección de
HTML y vista móvil. Defectos hallados y corregidos en esas pruebas: el pie se encimaba a las preguntas en pantallas bajas,
el último mensaje quedaba cortado bajo las sugerencias y la redacción "1 intentos".

No hay pruebas automáticas de la interfaz, ni auditoría de accesibilidad con lector de pantalla (hay etiquetas, `role="log"`,
foco gestionado y tema oscuro/claro, pero no se verificó con herramientas asistivas), ni pruebas en otros navegadores.
La interfaz no incluye la consola del agente humano (`GET /v1/handoffs` del backend queda sin pantalla).
