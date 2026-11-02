# Collectors programados de arXiv

Se usa un workflow independiente por categoría, pero todos llaman al mismo script y comparten el grupo de concurrencia `arxiv-api-requests`. El calendario escala los inicios cada 30 minutos y los repite cada seis horas, siempre en UTC:

| Categoría | Horas UTC | Workflow | SQLite | Índice JSON |
|---|---|---|---|---|
| `astro-ph` | 03:00, 09:00, 15:00, 21:00 | `arXiv astro-ph collector` | `library/astro-ph.sqlite` | `library/astro-ph.coverage.json` |
| `astro-ph.CO` | 03:30, 09:30, 15:30, 21:30 | `arXiv astro-ph.CO collector` | `library/astro-ph_CO.sqlite` | `library/astro-ph_CO.coverage.json` |
| `astro-ph.HE` | 04:00, 10:00, 16:00, 22:00 | `arXiv astro-ph.HE collector` | `library/astro-ph_HE.sqlite` | `library/astro-ph_HE.coverage.json` |
| `astro-ph.GA` | 04:30, 10:30, 16:30, 22:30 | `arXiv astro-ph.GA collector` | `library/astro-ph_GA.sqlite` | `library/astro-ph_GA.coverage.json` |
| `astro-ph.SR` | 05:00, 11:00, 17:00, 23:00 | `arXiv astro-ph.SR collector` | `library/astro-ph_SR.sqlite` | `library/astro-ph_SR.coverage.json` |
| `astro-ph.IM` | 05:30, 11:30, 17:30, 23:30 | `arXiv astro-ph.IM collector` | `library/astro-ph_IM.sqlite` | `library/astro-ph_IM.coverage.json` |
| `gr-qc` | 00:00, 06:00, 12:00, 18:00 | `arXiv gr-qc collector` | `library/gr-qc.sqlite` | `library/gr-qc.coverage.json` |
| `hep-th` | 00:30, 06:30, 12:30, 18:30 | `arXiv hep-th collector` | `library/hep-th.sqlite` | `library/hep-th.coverage.json` |
| `hep-ph` | 01:00, 07:00, 13:00, 19:00 | `arXiv hep-ph collector` | `library/hep-ph.sqlite` | `library/hep-ph.coverage.json` |
| `hep-ex` | 01:30, 07:30, 13:30, 19:30 | `arXiv hep-ex collector` | `library/hep-ex.sqlite` | `library/hep-ex.coverage.json` |
| `math-ph` | 02:00, 08:00, 14:00, 20:00 | `arXiv math-ph collector` | `library/math-ph.sqlite` | `library/math-ph.coverage.json` |

Los archivos de workflow están en `.github/workflows/arxiv-<slug>-collector.yml`. Todos también se pueden lanzar desde **Actions → workflow → Run workflow**; `max_days` limita los días nuevos o ausentes procesados en esa ejecución y vale 30 por defecto.

## Unidad de cobertura y recuperación

El día UTC es indivisible. La primera ejecución descubre una fecha inicial conservadora a partir del registro más antiguo de la categoría. Después, cada ciclo empieza en ayer UTC y recorre fechas hacia atrás, saltando solo días que `day_coverage` registre con `status='ok'`. Si falta una fecha, la consulta; si devuelve cero resultados, queda cubierta con total cero; si falla, se guarda el error y la siguiente ejecución vuelve a intentarlo. Las fechas ya cubiertas intercaladas con huecos se saltan sin ocultar esos huecos.

SQLite es la fuente de verdad. El sidecar `.coverage.json` se reconstruye desde `day_coverage` y no controla el cursor. El log común `logs/arxiv_collector.jsonl` es append-only y contiene una línea JSON por ejecución. Los IDs versionados son clave primaria dentro de cada base; si un artículo está asociado con más de una categoría, puede aparecer en las bases de esas categorías, pero no duplicado dentro de una misma base.

## Límites operativos

Las peticiones van en serie, con una pausa mínima de 3,2 segundos, páginas de máximo 2.000 resultados, límite lógico de 30.000 resultados por consulta, reintentos acotados y respeto de `Retry-After`. Los workflows usan el mismo grupo de concurrencia para que no haya dos de ellos consultando a la vez. GitHub puede retrasar una ejecución programada y su concurrencia solo coordina este repositorio; no coordina procesos externos bajo control del usuario. Si se acumulan ejecuciones pendientes, GitHub puede reemplazar una pendiente; la cobertura diaria persistente permite recuperar los días pendientes en ciclos posteriores.

La consulta histórica que usaba manualmente `data/arxiv_queries.sqlite` sigue separada y no alimenta estas bibliotecas. El recolector JSON general de `data/arxiv_records.json` también permanece separado.

## Referencias

- [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html): interfaz de consulta, Atom, versiones, paginación y límites.
- [Terms of Use for arXiv APIs](https://info.arxiv.org/help/api/tou.html): máximo de una petición cada tres segundos y una sola conexión activa bajo control del usuario.
