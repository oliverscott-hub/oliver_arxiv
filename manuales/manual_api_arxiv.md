# Manual operativo de la API de arXiv

**Elaborado:** 2 de noviembre de 2026  
**Uso:** consultas de metadatos para las tareas que encargue el Documentalista.  
**Fuente principal:** documentación y términos oficiales de arXiv enlazados al final. Revísalos antes de automatizar o si cambia la política.

## 1. Qué API es y qué devuelve

La API clásica de búsqueda de arXiv se consulta en:

```text
https://export.arxiv.org/api/query
```

Es una interfaz HTTP de solo lectura. Acepta `GET` y `POST` y devuelve un feed **Atom 1.0/XML** con cero o más registros `<entry>`. No requiere cuenta, clave ni token para consultas públicas normales. La API da acceso programático a metadatos y resultados de búsqueda; no es necesario descargar los PDF para identificar papers.

Campos habituales de cada registro:

- `<id>`: URL del abstract y, normalmente, identificador/version devuelta.
- `<published>`: fecha de la primera versión (v1).
- `<updated>`: fecha de la versión recuperada.
- `<title>`, `<summary>`, uno o más `<author>`.
- `<category>`: categorías; `<arxiv:primary_category>` identifica la principal.
- Extensiones de arXiv, cuando existen: comentario, referencia de revista, DOI, etc.

La respuesta también informa el total estimado de resultados (`opensearch:totalResults`), el índice inicial y cuántos entregó esa página. Si hay un error, puede venir como una entrada Atom de error: no supongas que HTTP 200 significa que la consulta fue válida; inspecciona el XML.

## 2. Regla de cortesía y acceso seguro

La condición operativa fundamental, según los términos oficiales consultados en esta fecha:

1. **No más de una solicitud cada 3 segundos**, para todas las APIs clásicas (incluida la API de búsqueda, OAI-PMH y RSS).
2. **Una sola conexión activa a la vez** bajo nuestro control. No paralelizar, no lanzar workers concurrentes ni ejecutar varias paginaciones a la vez.
3. Los límites son agregados a través de todas las máquinas bajo nuestro control: no cambiar de IP, máquina, proxy o cuenta para esquivarlos. No intentar sortearlos.
4. Los límites pueden cambiar. Comprobar los términos antes de poner un proceso recurrente o de gran volumen en marcha. Si el caso requiere más frecuencia, contactar el soporte de arXiv.
5. Cachear las respuestas. La documentación indica que los resultados de la API cambian con el ciclo diario de anuncios; no repetir la misma consulta varias veces al día sin motivo.
6. Si hay HTTP 429, 5xx, error de red o respuesta mal formada: detener el ritmo normal; respetar `Retry-After` si viene y aplicar espera creciente antes de reintentar. Nunca hacer un bucle rápido. Limitar reintentos y parar si persiste.
7. Afinar búsquedas antes de paginar. arXiv recomienda refinar consultas con más de 1.000 resultados o usar páginas pequeñas. El máximo documentado es 30.000 resultados totales por llamada lógica, en páginas de hasta 2.000; no solicitar páginas mayores de 2.000. Para cargas superiores, utilizar el mecanismo de acceso masivo apropiado y sus propias reglas.

Estas prácticas reducen cargas accidentales, pero ningún manual puede garantizar que nunca haya restricciones: arXiv se reserva la posibilidad de limitar/bloquear uso que amenace la operación o intente evadir los límites.

## 3. Qué hace falta

Para una consulta manual solo hace falta un navegador. Para automatizar:

- conexión a Internet;
- cliente HTTP que permita timeout y reporte de códigos de estado;
- parser XML/Atom;
- reloj y registro de solicitudes para mantener el intervalo global mínimo de tres segundos;
- caché local persistente (por consulta/URL normalizada o por ID y versión);
- política de reintento acotado que respete el límite también entre reintentos.

Python 3 basta con su biblioteca estándar `urllib` y `xml.etree.ElementTree`. No se requiere instalar un paquete adicional para el ejemplo de abajo.

## 4. Construir peticiones

### Parámetros principales

| Parámetro | Uso |
|---|---|
| `search_query` | Consulta por campos de metadatos. |
| `id_list` | Lista de identificadores separada por comas; úsala para recuperar IDs conocidos, sobre todo si importa tratar versiones correctamente. |
| `start` | Índice inicial, empieza en 0. |
| `max_results` | Tamaño de página; predeterminado 10, usa como máximo 2.000. |
| `sortBy` | `relevance`, `lastUpdatedDate` o `submittedDate`. |
| `sortOrder` | `ascending` o `descending`. |

Si se proporcionan `search_query` e `id_list` a la vez, el resultado son los IDs de `id_list` que además cumplen la consulta. El manual oficial recomienda `id_list` en lugar de `search_query=id:...` para manejar correctamente las versiones.

### Prefijos de búsqueda

- `ti:` título
- `au:` autor
- `abs:` resumen
- `co:` comentario
- `jr:` referencia de revista
- `cat:` categoría
- `rn:` número de informe
- `all:` todos esos campos

Operadores booleanos documentados: `AND`, `OR`, `ANDNOT`; se pueden agrupar expresiones con paréntesis y buscar frases entre comillas. Usa un cliente que codifique parámetros URL; no concatener texto arbitrario directamente en la URL.

El filtro temporal documentado es `submittedDate:[YYYYMMDDHHMM+TO+YYYYMMDDHHMM]`, con horas y minutos GMT/UTC. Es fecha de envío, no necesariamente día de anuncio. Conviene registrar la zona horaria y los extremos usados.

### Ejemplos

Una consulta sencilla (se puede pegar en el navegador):

```text
https://export.arxiv.org/api/query?search_query=cat%3Acs.LG+AND+ti%3Atransformer&start=0&max_results=25
```

Recuperar IDs concretos:

```text
https://export.arxiv.org/api/query?id_list=2301.00001,2301.00002
```

En Python, pasa parámetros separados para que la biblioteca los codifique correctamente:

```python
from urllib.parse import urlencode

params = {
    "search_query": 'cat:cs.LG AND ti:"transformer"',
    "start": 0,
    "max_results": 25,
}
url = "https://export.arxiv.org/api/query?" + urlencode(params)
```

Para consultas extensas se puede usar `POST` según el manual, evitando URLs enormes. El método POST **no** autoriza mayor frecuencia ni elimina la obligación del intervalo.

## 5. Paginación y control de cobertura

1. Empieza con una consulta lo más específica posible, y una página pequeña (por ejemplo 25–100).
2. Lee `totalResults`; conserva la consulta y el valor para el informe.
3. Si el total supera 1.000, refina por categoría, fechas o campos cuando sea razonable. Si realmente necesitas todos los resultados, pagina con `start=0, 2000, 4000...` y `max_results=2000` como máximo.
4. Solicita una página cada vez, espera al menos 3 segundos desde la solicitud anterior, y detente cuando no haya entradas o se alcance el total observado.
5. El total puede cambiar mientras se consulta el repositorio. Conserva resultados por ID, detecta duplicados entre páginas y revisa si el total cambió; una paginación en un corpus vivo no es una instantánea transaccional.
6. La API limita a 30.000 el número de resultados en una llamada lógica. Si el total es mayor, divide la consulta en rangos/subconsultas no solapados y documenta la estrategia; para cosecha integral, evalúa OAI-PMH en vez de forzar la API de búsqueda.

El orden predeterminado de la API es relevancia y puede diferir del buscador HTML. Si el orden importa, establece `sortBy`/`sortOrder` y no asumas que el orden implícito equivale al de la web.

## 6. Cliente Python conservador de ejemplo

Ejemplo didáctico para una búsqueda moderada. Mantiene una solicitud a la vez, introduce pausas entre todas las solicitudes y usa reintentos acotados. Para un uso de producción, añade caché persistente y registro de URL, hora, estado y número de reintento.

```python
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://export.arxiv.org/api/query"
ATOM = "http://www.w3.org/2005/Atom"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"
MIN_GAP = 3.2  # margen adicional sobre el mínimo publicado de 3 s
last_request_at = 0.0


def fetch_page(params, max_retries=3):
    """Una petición cada vez; espera también antes del primer acceso del proceso."""
    global last_request_at
    url = BASE + "?" + urllib.parse.urlencode(params)

    for attempt in range(max_retries + 1):
        wait = MIN_GAP - (time.monotonic() - last_request_at)
        if wait > 0:
            time.sleep(wait)

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MalditoSigmaArxivResearch/1.0 (contacto: sustituir-por-email)"},
        )
        last_request_at = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
            root = ET.fromstring(body)
            error = root.find(f"{{{ATOM}}}entry/{{{ATOM}}}summary")
            if error is not None and "error" in (error.text or "").lower():
                raise RuntimeError(f"Respuesta de error de arXiv: {error.text}")
            return root
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                delay = max(MIN_GAP, float(retry_after)) if retry_after else MIN_GAP * (2 ** attempt)
            elif 500 <= exc.code < 600:
                delay = MIN_GAP * (2 ** attempt)
            else:
                raise  # 4xx de consulta: corregir petición, no reintentar a ciegas
        except (urllib.error.URLError, TimeoutError, ET.ParseError):
            if attempt >= max_retries:
                raise
            delay = MIN_GAP * (2 ** attempt)

        if attempt >= max_retries:
            raise RuntimeError("Se agotaron los reintentos; parar y revisar el servicio.")
        time.sleep(delay)


def entries(root):
    for entry in root.findall(f"{{{ATOM}}}entry"):
        error = entry.find(f"{{{ATOM}}}summary")
        if error is not None and "error" in (error.text or "").lower():
            raise RuntimeError(f"Error devuelto por arXiv: {error.text}")
        primary = entry.find(f"{{{ARXIV}}}primary_category")
        yield {
            "id_url": entry.findtext(f"{{{ATOM}}}id"),
            "published_v1": entry.findtext(f"{{{ATOM}}}published"),
            "updated_version": entry.findtext(f"{{{ATOM}}}updated"),
            "title": " ".join((entry.findtext(f"{{{ATOM}}}title") or "").split()),
            "authors": [a.findtext(f"{{{ATOM}}}name") for a in entry.findall(f"{{{ATOM}}}author")],
            "primary_category": primary.get("term") if primary is not None else None,
            "categories": [c.get("term") for c in entry.findall(f"{{{ATOM}}}category")],
        }


params = {
    "search_query": "cat:cs.LG AND ti:transformer",
    "start": 0,
    "max_results": 25,
    "sortBy": "submittedDate",
    "sortOrder": "descending",
}
root = fetch_page(params)
total = int(root.findtext(f"{{{OPENSEARCH}}}totalResults", default="0"))
records = list(entries(root))
print(f"Total indicado: {total}; página recibida: {len(records)}")
for record in records:
    print(record)
```

**Ajustes antes de reutilizar el código:** sustituir el contacto identificativo por un email monitorizado si se adopta este cliente; mantener el límite entre ejecuciones y equipos (el contador en memoria solo cubre este proceso); guardar respuestas y datos de consulta; no usar este ejemplo para hacer harvesting completo sin adaptar persistencia, reanudación, límites y monitorización. Un `User-Agent` identificable es una buena práctica operativa, pero no reemplaza los términos ni es una autorización especial.

## 7. Caché, errores y recuperación

- Persistir la respuesta XML original por URL normalizada y fecha/hora de obtención; conservar también parámetros, total, estado HTTP y huella/hash si se requiere auditoría.
- Reutilizar caché para la misma consulta durante el ciclo diario; volver a consultar solo cuando la tarea requiera actualizar resultados o verificar una ficha.
- Guardar cada página al recibirla, no esperar al final de una gran cosecha: si el proceso cae, reanudar desde el `start` pendiente y deduplicar por ID.
- En fallo temporal, esperar al menos tres segundos desde cualquier solicitud previa y usar backoff creciente (p. ej., 3.2, 6.4, 12.8 s) con pocos intentos. En 429, respetar `Retry-After` y no repetir en paralelo.
- En 400 u otro 4xx salvo 429, leer el mensaje de error y corregir consulta/parámetros; no repetir idéntica solicitud.
- Si hay errores repetidos, `Retry-After` largo, 5xx persistentes o señal de bloqueo, parar. No cambiar de IP ni multiplicar procesos. Revisar [estado de arXiv](https://status.arxiv.org/) y contactar soporte si hace falta.

## 8. Cuándo usar cada interfaz

- **API de búsqueda:** búsquedas puntuales por campos, categorías, fechas de envío, recuperación de IDs y páginas limitadas.
- **OAI-PMH:** sincronización/cosecha de metadatos a gran escala. La documentación oficial marca la API de búsqueda como menos adecuada para harvesting masivo. OAI expone principalmente la versión más reciente; el formato `arXivRaw` incluye historial. Su `datestamp` es fecha de modificación del registro y no siempre equivale a envío/reemplazo; no tratarlo como fecha editorial. Los términos de frecuencia también se aplican.
- **PDF y archivos fuente:** dirígase a arXiv y respete copyright/licencia. Los términos permiten recuperar metadatos descriptivos, pero no redistribuir los e-prints (PDF/fuentes) desde servidores propios salvo permiso del titular o licencia que lo autorice.

Para el trabajo editorial normal, recuperar metadatos y enlazar la ficha de arXiv suele bastar.

## 9. Lista de control antes de ejecutar

- [ ] He consultado términos y manual oficiales actuales.
- [ ] La consulta está acotada, codificada y documentada.
- [ ] La ejecución serializa solicitudes y cumple un mínimo global de 3 s entre cada una.
- [ ] No hay otro proceso/equipo propio consultando a la vez.
- [ ] Uso caché y no vuelvo a bajar el mismo resultado sin motivo.
- [ ] `max_results` ≤ 2.000; si hay más de 1.000 resultados, reviso si puedo refinar.
- [ ] Guardo XML y metadatos de auditoría de cada página.
- [ ] Los reintentos son limitados; respeto `Retry-After`; paro ante fallos sostenidos.
- [ ] Diferencio fecha de envío, fecha de actualización y fecha de anuncio.
- [ ] Compruebo ficha/historial y confirmo publicación fuera de arXiv cuando sea relevante.

## 10. Fuentes oficiales consultadas

- [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html) — endpoint, búsqueda, campos, paginación, Atom, errores y límites de la API clásica.
- [Terms of Use for arXiv APIs](https://info.arxiv.org/help/api/tou.html) — frecuencia, conexión única, evasión, usos permitidos y restricciones del contenido.
- [Open Archives Initiative (OAI-PMH) de arXiv](https://info.arxiv.org/help/oa/index.html) — opción de cosecha, metadatos, datestamps y sincronización.
- [Estado del servicio arXiv](https://status.arxiv.org/) — comprobar incidentes antes de diagnosticar fallos persistentes.

**Verificado el:** 2 de noviembre de 2026. Los límites y detalles de API son operativos y pueden cambiar; confirmar la documentación oficial antes de automatizar en el futuro.
