# Guía breve para buscar y verificar preprints en arXiv

**Fecha de elaboración:** 2 de noviembre de 2026  
**Ámbito:** protocolo de consulta; aplicar solo a los temas, intervalos y plazos que indique el Documentalista.

## Flujo de verificación

1. **Buscar y dejar rastro.** Usar la búsqueda avanzada de arXiv o su API; registrar fecha/hora de acceso, consulta exacta, campos (título `ti`, autor `au`, resumen `abs`, categoría `cat`), filtros temporales y orden. Para reproducibilidad, guardar la URL de consulta y revisar también la página abstract de cada resultado. La API busca en metadatos y permite filtrar por `submittedDate`; no confundir fecha de envío con fecha de anuncio.
2. **Fijar el registro.** Anotar el identificador base (`YYMM.NNNNN` o identificador antiguo), la versión vigente (`vN`) y el enlace con versión explícita (`/abs/IDvN`). Sin sufijo, la página muestra la versión más reciente. Revisar el historial de versiones y registrar por separado fecha de v1 y fecha de la versión consultada. En la API, `published` corresponde a v1 y `updated` a la versión devuelta.
3. **Comprobar metadatos.** Transcribir título, autores, categoría primaria y categorías secundarias/cruces, fechas y DOI o referencia de revista si aparecen. Las categorías secundarias pueden ser cruces (`cross-listings`); registrar la primaria por separado. Consultar la ficha original si la API omite o resume algún dato.
4. **Buscar señales de retirada y publicación.** Leer el aviso e historial: una retirada crea una versión nueva marcada como retirada; las versiones anteriores pueden seguir disponibles. Anotar literalmente el estado y motivo público, sin inferir que todo el trabajo carece de valor. La referencia de revista/DOI en arXiv es una pista, no prueba suficiente del estado editorial actual: verificar la publicación en la revista, editorial o DOI/Crossref y distinguir preprint de versión publicada.
5. **Cerrar la ficha.** Entregar al Documentalista identificador y versión, título, autores, fechas, categorías, enlace, consulta/filtros/fecha de acceso y cualquier ambigüedad, cambio de versión, cruce, retirada o publicación detectada.

## Límites de la fuente

- arXiv es un repositorio de preprints y otros trabajos; su moderación comprueba pertinencia y estándares básicos de presentación, pero no equivale a revisión por pares.
- Metadatos como título, autores, comentarios, DOI o referencia de revista pueden faltar, estar desactualizados o cambiar entre versiones. La fecha de v1 identifica el envío inicial; la versión consultada tiene su propia fecha de actualización. El día de anuncio y el de envío no siempre coinciden.
- Una búsqueda por palabras/categorías puede omitir trabajos relevantes por términos, clasificación, idioma, indexación o límites de consulta; conservar consulta y alcance para que el Documentalista valore cobertura.
- La retirada no borra necesariamente el historial ni los textos previos. Hay que identificar qué versión está retirada y no presentar una versión antigua como vigente.
- Una ficha de arXiv por sí sola no establece aceptación, publicación, corrección o retractación en una revista, ni valida resultados científicos.

## Fuentes oficiales

- [Manual de la API de arXiv](https://info.arxiv.org/help/api/user-manual.html) — campos, categorías, fechas y límites/uso de consultas.
- [Versiones en arXiv](https://info.arxiv.org/help/versions.html) — identificadores, historial y versiones.
- [Retirar o retractar un envío](https://info.arxiv.org/help/withdraw.html) — efecto y visibilidad de una retirada.
- [Cruces de categoría](https://info.arxiv.org/help/cross.html) — categorías primarias y cross-listings.
- [Disponibilidad de envíos](https://info.arxiv.org/help/availability.html) — moderación, envío y anuncio.
