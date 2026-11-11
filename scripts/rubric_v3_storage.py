"""Minimal version 3 rubric storage."""
import re
import sqlite3

WEIGHTS = (20, 40, 20, 20)
LINES = ('T', 'Q1', 'E1', 'J1', 'Q2', 'E2', 'J2', 'Q3', 'E3', 'J3', 'Q4', 'E4', 'J4')
COLUMNS = ('id', 'modelo', 'version_rubrica', 'intentos', 'puntuacion', *LINES)


def normalize(raw, abstract=None):
    lines = raw.strip().splitlines()
    if len(lines) != 13:
        raise ValueError(f'Expected 13 lines, got {len(lines)}')
    values = {}
    for key, line in zip(LINES, lines):
        if not line.startswith(key + '='):
            raise ValueError(f'Expected {key} line')
        value = line[len(key) + 1:].strip()
        if key.startswith('Q') or key == 'T':
            value = value.upper()
            if not re.fullmatch('[ABCDU]' if key != 'T' else '[TEMSPU]', value):
                raise ValueError(f'Invalid {key}')
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key.startswith('J'):
                value = value.replace('"', "'")
        values[key] = value
    for i in range(1, 5):
        q = values[f'Q{i}']
        if q in 'ABU':
            values[f'E{i}'] = '-'
            values[f'J{i}'] = '-'
        elif not values[f'J{i}'] or values[f'J{i}'] == '-':
            raise ValueError(f'Invalid J{i} for {q}')
    return values


def parse(raw, abstract=None):
    return normalize(raw, abstract)


def metrics(values):
    if any(values[f'Q{i}'] == 'U' for i in range(1, 5)):
        return {'score_cents': None}
    weighted = sum(w * (ord(values[f'Q{i}']) - 65) for i, w in enumerate(WEIGHTS, 1))
    return {'score_cents': (weighted * 10 + 1) // 3}


def open_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('PRAGMA busy_timeout=30000')
    fields = ', '.join(f'{name} TEXT' for name in LINES)
    db.execute(f'''CREATE TABLE IF NOT EXISTS evaluations (
        id TEXT PRIMARY KEY, modelo TEXT NOT NULL,
        version_rubrica INTEGER NOT NULL CHECK(version_rubrica=3),
        intentos INTEGER NOT NULL DEFAULT 0 CHECK(intentos IN (-1,0,1,2,3)),
        puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000), {fields})''')
    names = [row[1] for row in db.execute('PRAGMA table_info(evaluations)')]
    if 'intentos' not in names:
        db.execute('ALTER TABLE evaluations ADD COLUMN intentos INTEGER NOT NULL DEFAULT 1 CHECK(intentos IN (-1,1,2,3))')
        names = [row[1] for row in db.execute('PRAGMA table_info(evaluations)')]
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='evaluations'").fetchone()[0]
    if names != list(COLUMNS) or '(-1,0,1,2,3)' not in schema:
        db.execute(f'''CREATE TABLE evaluations_reordered (
            id TEXT PRIMARY KEY, modelo TEXT NOT NULL,
            version_rubrica INTEGER NOT NULL CHECK(version_rubrica=3),
            intentos INTEGER NOT NULL DEFAULT 0 CHECK(intentos IN (-1,0,1,2,3)),
            puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000), {fields})''')
        selected = ','.join('CASE WHEN T IS NOT NULL THEN 0 ELSE intentos END' if key == 'intentos' else key for key in COLUMNS)
        db.execute(f'INSERT INTO evaluations_reordered ({",".join(COLUMNS)}) SELECT {selected} FROM evaluations')
        db.execute('DROP TABLE evaluations')
        db.execute('ALTER TABLE evaluations_reordered RENAME TO evaluations')
    db.commit()
    return db


def mark_attempt(db, *, paper_id, model):
    row = db.execute('SELECT intentos,T FROM evaluations WHERE id=?', (paper_id,)).fetchone()
    if row and row[0] in (0, -1):
        return None
    attempt = min(row[0] + 1, 3) if row else 1
    if attempt > 3:
        return None
    db.execute('''INSERT INTO evaluations (id,modelo,version_rubrica,intentos)
        VALUES (?,?,3,?) ON CONFLICT(id) DO UPDATE SET intentos=excluded.intentos''',
        (paper_id, model, attempt))
    db.commit()
    return attempt


def mark_discarded(db, paper_id):
    db.execute('UPDATE evaluations SET intentos=-1 WHERE id=? AND intentos=3 AND T IS NULL', (paper_id,))
    db.commit()


def save_response(db, *, arxiv_id, model, abstract, raw, **unused):
    try:
        values = normalize(raw, abstract)
    except ValueError as error:
        return None, str(error)
    score = metrics(values)['score_cents']
    row = (arxiv_id, model, 3, 0, score, *(values[key] for key in LINES))
    placeholders = ','.join('?' for _ in COLUMNS)
    updates = ','.join(f'{key}=excluded.{key}' for key in ('intentos', 'puntuacion', *LINES))
    db.execute(f'INSERT INTO evaluations ({",".join(COLUMNS)}) VALUES ({placeholders}) '
               f'ON CONFLICT(id) DO UPDATE SET {updates} WHERE evaluations.intentos IN (1,2,3)', row)
    db.commit()
    return score, None
