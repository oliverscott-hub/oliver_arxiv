# Rúbrica v3: almacenamiento vigente

Ambos modelos reciben la instrucción de `scripts/rubric_v3_prompt.txt` y solo el abstract. Las respuestas se normalizan antes de guardarse. Para cada `JX`, se retiran comillas externas simples o dobles, se sustituyen comillas dobles internas por simples y se conserva el texto. Para `QX=A`, `B` o `U`, `EX` y `JX` quedan como el texto literal `-`. Las evidencias de `EX` se conservan para trazabilidad sin exigir que coincidan literalmente con el abstract.

Una respuesta que no tenga exactamente trece líneas se descarta sin crear una fila. El artículo queda pendiente para otra ejecución. Las otras respuestas inválidas también se descartan. Los dos workflows mantienen sus cron, ejecución manual y `repository_dispatch`.

Cada base contiene una tabla `evaluations` con estas columnas, en orden: `id` (identificador arXiv, clave primaria), `modelo`, `version_rubrica`, `puntuacion`, `T`, `Q1`, `E1`, `J1`, `Q2`, `E2`, `J2`, `Q3`, `E3`, `J3`, `Q4`, `E4`, `J4`. `puntuacion` guarda centésimas de la escala 0–10 como entero de 0 a 1000. Si alguna `QX` es `U`, queda `NULL`. Los pesos son 20/40/20/20. La clasificación `T` no afecta la puntuación.

Las SQLite anteriores a esta corrección están archivadas en `informes/rubrica_v3_sqlite_historico_2026-11-07/` fuera del repositorio. Esas copias conservan las respuestas originales y los intentos fallidos.
