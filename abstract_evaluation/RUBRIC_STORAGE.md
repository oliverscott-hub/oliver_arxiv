# Almacenamiento de la rúbrica v1

Las bases `rubric_qwen3_8b.sqlite` y `rubric_openscholar_8b.sqlite` guardan los experimentos nuevos por separado. Las bases anteriores `abstract_importance_mod0.sqlite` y `abstract_importance_mod1.sqlite` y sus copias no se modifican.

La tabla `evaluations` comienza con `arxiv_id`, `modelo`, `version_rubrica` y `nota_milesimas`. La nota se muestra dividiendo por 1000 (por ejemplo, `7167` equivale a `7.167/10`). Su valor es nulo si la respuesta no pasa la validación. Cada ejecución añade una fila; `id` identifica el intento y permite comparar repeticiones sin sobrescribir datos.

Después figuran `variante` (`A`, `B` o `C`), `version_pesos`, `pesos_json`, la huella SHA-256 del abstract, las siete categorías `I1`–`I7` y, para B, los siete pares `I*_evidence_type` y `I*_evidence_text`. También se guardan por separado tipo de contribución, métodos (lista JSON), estado del resultado, apoyo, limitaciones, estado de la afirmación, resumen y justificación global (A/C). `response_raw` conserva la salida exacta del modelo y `validation_error` explica cualquier rechazo. Se incluyen cobertura, notas mínima y máxima, número de criterios indeterminados, hora UTC, latencia y recuentos de tokens cuando estén disponibles.

Las columnas I1–I7 conservan las elecciones originales del modelo. Si cambian los pesos, hay que crear una nueva versión de pesos y recalcular la nota a partir de esas columnas; la nota histórica y los pesos usados en ella permanecen registrados. Un cambio en definiciones u opciones de la rúbrica requiere incrementar `version_rubrica`.

El módulo `scripts/rubric_storage.py` valida A y B, calcula la nota en código y guarda las respuestas. La variante C acepta la nota directa como referencia y deja las dimensiones vacías. Las bases se han inicializado vacías: los evaluadores de producción siguen pausados y todavía no llaman al módulo nuevo.

Consulta de ejemplo:

```sql
SELECT arxiv_id, modelo, version_rubrica,
       printf('%.3f', nota_milesimas / 1000.0) AS nota,
       variante, I1, I2, I3, I4, I5, I6, I7
FROM evaluations
WHERE validation_error IS NULL
ORDER BY nota_milesimas DESC;
```
