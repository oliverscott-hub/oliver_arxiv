# arXiv astro-ph.CO collector

## Diseño

El workflow **arXiv astro-ph.CO collector** corre cada 6 horas en UTC (`00:00`, `06:00`, `12:00` y `18:00`) y también puede lanzarse manualmente. Comparte el grupo de concurrencia `arxiv-api-requests` con los otros workflows de arXiv de este repositorio, de modo que no intente consultar la API a la vez que ellos. Esas horas son una frecuencia de ejecución, no una promesa de inicio exacto: GitHub Actions puede retrasar tareas programadas.

Cada ejecución excluye el día UTC actual y procesa días completos, empezando por ayer y avanzando hacia fechas anteriores. El máximo inicial es 30 días por ejecución; el input manual permite cambiarlo. Descubre una vez el día del registro más antiguo de `astro-ph.CO` mediante una búsqueda ascendente y lo conserva en `collector_state`, incluyendo un día de margen anterior.

El proceso consulta un día entero con `submittedDate:[YYYYMMDD0000 TO YYYYMMDD2359]`. Pide páginas secuenciales de hasta 2.000 entradas y espera al menos 3,2 segundos entre solicitudes. Si el total lógico supera 30.000, la paginación no coincide con el total informado, o aparece un error persistente, el día queda con estado `error` y no se considera cubierto. No se subdivide el día: el día UTC es la unidad acordada de cobertura. Los registros de páginas ya descargadas pueden quedar guardados, pero la clave primaria hace que el reintento sea idempotente.

El siguiente ciclo comienza por ayer y retrocede buscando el día no cubierto más reciente. Las fechas ya cubiertas se omiten; los huecos y días fallidos se consultan al llegar a ellos. Así, la cobertura depende del registro diario, no de que existan papers para una fecha: un día consultado correctamente con cero resultados se guarda como `ok`, total cero.

## Archivos

- `library/astro-ph_CO.sqlite`: fuente de verdad, papers y cobertura diaria.
- `library/astro-ph_CO.coverage.json`: resumen regenerado desde SQLite; lectura cómoda, no controla la recolección.
- `logs/arxiv_collector.jsonl`: log común acumulativo, una línea JSON por ejecución, incluyendo fechas procesadas y fallos.

En la tabla `papers`, `arxiv_id` (incluida su versión, por ejemplo `2601.12345v2`) es la clave primaria. `work_id` (`2601.12345`) agrupa versiones pero no deduplica versiones diferentes. La inserción repetida de una misma versión actualiza sus metadatos y conserva `first_seen_utc`. La API de búsqueda devuelve los registros/versiones que correspondan a la consulta; esta recolección no afirma reconstruir todas las versiones históricas de cada trabajo. El manual oficial explica que `<published>` es la fecha de la primera versión, `<updated>` la de la versión recuperada, y que las versiones concretas se solicitan por ID versionado.

`day_coverage` contiene una fila por fecha UTC consultada: estado, total informado por la API, cantidad capturada, solicitudes/páginas, hora y error. Solo `status='ok'` significa que el día quedó cubierto. `query_runs` audita cada ejecución y su estado.

## Alcance y límites

Se guardan los metadatos Atom que devuelve la API, no PDFs ni fuentes. `submittedDate` describe la fecha de envío consultada y no debe confundirse con el día oficial de anuncio. La API de búsqueda no es una instantánea transaccional; los resultados pueden cambiar. La próxima ejecución revisita las fechas nuevas; el histórico ya cubierto no se vuelve a rastrear automáticamente, salvo que se reabra manualmente su cobertura.

La política publicada por arXiv recomienda una pausa de tres segundos entre llamadas consecutivas, limita `max_results` a 2.000 por página y el resultado de una llamada lógica a 30.000. El proceso usa paginación serial, 3,2 segundos de espera, reintentos acotados con backoff y respeta `Retry-After`. Las reglas actuales deben revisarse si arXiv cambia sus términos.

## Uso manual

En GitHub: **Actions → arXiv astro-ph.CO collector → Run workflow**. `max_days` limita cuántos días nuevos/faltantes como máximo se procesan en esa ejecución; usa un valor razonable para mantenerla dentro del tiempo límite de Actions.

## Fuentes oficiales

- [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html): interfaz de consulta, filtros, Atom, paginación y límites.
- [Terms of Use for arXiv APIs](https://info.arxiv.org/help/api/tou.html): condiciones de uso y límites operativos vigentes.
