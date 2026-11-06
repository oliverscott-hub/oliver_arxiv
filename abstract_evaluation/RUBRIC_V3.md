# Rúbrica v3

La instrucción de producción está en `scripts/rubric_v3_prompt.txt`. Conserva la propuesta recibida, con la errata «nine lines» corregida a «thirteen lines» porque el formato contiene una línea `T` y tres líneas por cada una de las cuatro preguntas.

Los dos modelos reciben solo la instrucción y el abstract. Responden `T`, `Q1/E1/J1`, ..., `Q4/E4/J4`. Las bases independientes `rubric_v3_qwen3_8b.sqlite` y `rubric_v3_openscholar_8b.sqlite` guardan esos campos en ese orden tras `arxiv_id`, `modelo` y `puntuacion_centesimas`. También conservan la respuesta original, errores, huella del abstract, hora, latencia, tokens, versión, pesos, cobertura e intervalo. Las SQLite anteriores son históricas y no se modifican.

El validador exige trece líneas, opciones permitidas, fragmentos exactos del abstract y la presencia de `J` solo para `C` o `D`. Una respuesta inválida se conserva sin puntuación. Las letras se aceptan sin distinguir mayúsculas y minúsculas.

La puntuación, cuando Q1–Q4 están determinados, es `10/3 × (20×nivel(Q1)+40×nivel(Q2)+20×nivel(Q3)+20×nivel(Q4))/100`, con A=0, B=1, C=2 y D=3. Se guarda redondeada a dos decimales en centésimas. Si hay alguna `U`, `puntuacion_centesimas` queda nula y se guardan cobertura, mínimo y máximo posibles. `T` no influye en la nota.

Los workflows ejecutan la v3 por cron a las 02:00, 10:00 y 18:00 UTC; admiten ejecución manual y `repository_dispatch`. `max_papers` permite limitar una prueba manual.
