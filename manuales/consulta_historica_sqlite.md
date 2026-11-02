# Consulta histórica de arXiv a SQLite

## Lanzar la consulta

En GitHub, abre **Actions → Consultar rango de fechas de arXiv y guardar en SQLite → Run workflow**. El workflow consulta `astro-ph.CO`, con el intervalo UTC indicado en `from_day` y `to_day`. Los valores iniciales son `2026-10-01` y `2026-10-10`; ambos días se incluyen.

La búsqueda usa `submittedDate` y orden descendente por fecha de envío. Solicita páginas de hasta 2.000 resultados, procesa una página a la vez y mantiene al menos 3,2 segundos entre peticiones. La ejecución es manual; no añade otro cron.

## Base de datos

El resultado se guarda en `data/arxiv_queries.sqlite` y se confirma en el repositorio con un commit de GitHub Actions.

- `query_runs`: fechas, categoría/segmento, consulta exacta, endpoint, total, estado y recuento de la ejecución.
- `query_pages`: hora exacta, índice inicial, HTTP, tamaño y cantidad capturada por página, reintentos y errores.
- `entries`: metadatos de cada artículo. `author_json`, `link_json`, `category_json`, `arxiv_primary_category_json` y `source_json` contienen arrays u objetos JSON.

`published` es la fecha de envío de la primera versión y `updated` la de la versión devuelta por la API. `abstract.length` se calcula sobre el resumen normalizado. `arxiv_status_comment` queda nulo porque `arxiv:comment` es texto libre, no un estado editorial estructurado.

La captura no descarga PDFs ni archivos fuente. La búsqueda histórica representa los resultados que la API devuelve para el rango y momento de ejecución; una reejecución crea un nuevo `query_run_id` y conserva una captura separada.

## Biblioteca incremental astro-ph.CO

El workflow periódico y manual ya implementado se documenta en [collector_astro_ph_CO.md](collector_astro_ph_CO.md). Su base es `library/astro-ph_CO.sqlite`; el índice legible `library/astro-ph_CO.coverage.json` se deriva desde SQLite, y el log acumulativo común está en `logs/arxiv_collector.jsonl`.

La clave primaria de papers es el identificador versionado completo de arXiv (`2601.12345v2`). `work_id` conserva el identificador sin sufijo de versión para agrupar versiones, pero no para deduplicarlas. La cobertura se registra por día UTC, incluso cuando la API responde correctamente con cero resultados.
