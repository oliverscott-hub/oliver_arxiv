# Recolector de metadatos de arXiv — Maldito Sigma

Repositorio de trabajo dedicado al seguimiento periódico de metadatos de arXiv en once categorías de astrofísica, física de altas energías y física matemática.

## Funcionamiento

- GitHub Actions ejecuta el recolector dos veces al día, a los minutos 19 de las horas UTC divisibles por 12 (`00:19` y `12:19` UTC). El horario evita el minuto cero, cuando GitHub advierte que puede haber más carga; la ejecución programada puede retrasarse.
- Cada ejecución consulta una ventana móvil de 36 horas. Así, si falla una de las ejecuciones de 12 horas, la siguiente vuelve a cubrir el intervalo.
- Se guardan solo metadatos, en `data/arxiv_records.json`, con identificador, versión más reciente observada, título, autores, resumen, fechas, categorías, DOI/referencia cuando consten, consulta y fecha de acceso.
- Se deduplican trabajos por identificador arXiv y se conserva la lista de versiones observadas. El catálogo completo vive en Git, por lo que las versiones anteriores del propio fichero quedan en el historial de commits.
- No se descargan PDFs ni fuentes.

## Categorías vigiladas

`astro-ph`, `astro-ph.CO`, `astro-ph.HE`, `astro-ph.GA`, `astro-ph.SR`, `astro-ph.IM`, `gr-qc`, `hep-th`, `hep-ph`, `hep-ex`, `math-ph`.

La configuración editable está en `config.json`. Se consulta el campo `submittedDate` para la ventana móvil; el tiempo se expresa en UTC y no es necesariamente el día oficial de anuncio de arXiv.

## Política de peticiones

El cliente hace peticiones en serie, espera un mínimo de 3,2 segundos, limita las páginas a 2.000 resultados, cachea el resultado en el catálogo y usa reintentos acotados con espera creciente. Respeta `Retry-After` en HTTP 429. Si la consulta excede 30.000 resultados o el XML está corrupto, se detiene sin reemplazar el catálogo anterior.

Los términos vigentes de arXiv indican como máximo una solicitud cada tres segundos y una sola conexión simultánea para todas las máquinas bajo nuestro control. Comprueba siempre los [términos oficiales](https://info.arxiv.org/help/api/tou.html) y el [manual de la API](https://info.arxiv.org/help/api/user-manual.html) antes de cambiar este proceso.

## Activación

Una vez que este directorio esté en un repositorio GitHub, el workflow `.github/workflows/arxiv.yml` debe estar en la rama predeterminada. Revisa en Settings → Actions → General que el permiso `GITHUB_TOKEN` permita escribir contenidos. El workflow solo escribe en `data/arxiv_records.json`.

Para ejecución local: `python scripts/collect_arxiv.py`.
