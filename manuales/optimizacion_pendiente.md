# Lista de modificaciones pendientes para optimizar el repo

Esta lista registra candidatos de limpieza. No se borra nada de ella hasta decidirlo expresamente.

## Pendiente

- [ ] **Retirar el log común `logs/arxiv_collector.jsonl`.** Es redundante con `query_runs` y `day_coverage` de cada SQLite, y hace que todos los workflows escriban en el mismo archivo. Si se aprueba, quitar también el append en `scripts/collect_arxiv_category.py`, sus referencias en los 10 workflows y las menciones en manuales/README. No retirar todavía.

## Candidatos a revisar

- [ ] **Recolector JSON general:** `.github/workflows/arxiv.yml`, `scripts/collect_arxiv.py` y `data/arxiv_records.json`. Duplica consultas a las diez categorías que ya guardan datos en `library/*.sqlite`, y mantiene un segundo catálogo. Antes de retirarlo, comprobar que ningún proceso o consumidor externo dependa de ese JSON.
- [ ] **Acción de consulta histórica puntual:** `.github/workflows/arxiv-date-query.yml`, `scripts/query_arxiv_to_sqlite.py`, `data/arxiv_queries.sqlite` y `manuales/consulta_historica_sqlite.md`. Fue creada para una captura acotada de `astro-ph.CO` y se solapa temporalmente con la biblioteca permanente. Conservar si se quiere mantener la captura como auditoría o ejemplo reproducible.
- [ ] **Manuales solapados:** `manuales/collector_astro_ph_CO.md` repite buena parte de `manuales/collectors_arxiv.md`. Comparar y fusionar cualquier detalle exclusivo de `astro-ph.CO`; después actualizar la referencia de `manuales/consulta_historica_sqlite.md` antes de retirar el manual específico.
- [ ] **`.gitkeep` de `informes/`:** ya hay informes versionados en esa carpeta, por lo que el marcador vacío probablemente dejó de ser necesario.
- [ ] **Workflows repetidos:** los diez workflows de categoría comparten pasos. Evaluar convertir la lógica común en un workflow reutilizable manteniendo un disparador y horario claro por categoría.

## Mantener por ahora

- Los diez `library/*.sqlite` son los catálogos activos y su estado de cobertura.
- Los diez `library/*.coverage.json` se derivan de SQLite, pero permiten inspeccionar fácilmente el conteo por día y fueron solicitados para visualizar la cobertura.
- `scripts/collect_arxiv_category.py`, `config.json`, los diez workflows de categoría, `AGENTS.md`, `README.md` y el manual de la API sostienen la recolección activa, sus reglas y su uso.
- `credenciales/.env` está ignorado por Git y no forma parte del repo; mantener esa protección y no versionar el secreto.
- Los informes fechados son documentación/auditoría, no archivos necesarios para ejecutar los workflows.
