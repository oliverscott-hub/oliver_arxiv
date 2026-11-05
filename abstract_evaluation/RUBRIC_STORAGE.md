# Rúbrica científica v1: elecciones A–D o U

Cada modelo recibe exclusivamente las instrucciones de la rúbrica y el abstract. Para cada dimensión I1–I7 selecciona una letra: A, B, C o D para las cuatro categorías ordenadas; U significa información insuficiente. La salida son exactamente siete líneas `I1=C` … `I7=B`. El lector acepta minúsculas y espacios alrededor de `=`, normaliza las letras a mayúsculas y exige una sola opción por dimensión. Una gramática limita la generación a esas siete líneas; el parser independiente vuelve a comprobarlas.

La rúbrica y sus pesos siguen en **versión 1**: 15, 15, 25, 10, 15, 10 y 10 %. El procedimiento de generación tiene `version_prompt=2` para distinguirlo del experimento JSON inicial. Python traduce letras a categorías, asigna niveles 0–3, usa 1,5 para U y calcula la nota 0–10 con tres decimales. Guarda cobertura, mínimo, máximo y número de dimensiones indeterminadas. El modelo no produce una nota.

Cada modelo tiene una base independiente: `rubric_qwen3_8b.sqlite` y `rubric_openscholar_8b.sqlite`. La tabla `evaluations` comienza con `arxiv_id`, `modelo`, `version_rubrica` y `nota_milesimas` (`7167` representa `7.167/10`). Después almacena las siete letras y las siete categorías semánticas en columnas separadas, pesos y versiones, SHA-256 del abstract, respuesta textual original, error de validación, fecha UTC, latencia y tokens. Se permite un registro válido por arxiv_id y versión de rúbrica; los intentos inválidos se conservan sin nota. Las bases históricas `abstract_importance_mod0.sqlite`, `abstract_importance_mod1.sqlite` y sus copias de seguridad no se alteran.

El evaluador recorre primero las prioridades por modelo y después los días UTC más recientes alternando categorías. Reintenta una vez una respuesta inválida; si tres papers consecutivos no producen ninguna nota válida, termina con error. Publica checkpoints cada 30 minutos. Los workflows admiten cron 02:00/10:00/18:00 UTC, `repository_dispatch` y ejecución manual. En ejecución manual `max_papers` permite una prueba corta; `0` deja la ejecución completa.

Las explicaciones y la referencia de puntuación directa se evaluarán por separado, sin modificar la nota calculada por estas siete elecciones. Los JSON previos A/B/C fueron descartados de estas dos bases al reiniciarlas para este formato.

```sql
SELECT arxiv_id, modelo, version_rubrica,
       printf('%.3f', nota_milesimas/1000.0) AS nota,
       I1_choice,I2_choice,I3_choice,I4_choice,I5_choice,I6_choice,I7_choice,
       I1,I2,I3,I4,I5,I6,I7
FROM evaluations WHERE validation_error IS NULL
ORDER BY nota_milesimas DESC;
```
