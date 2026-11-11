"""Phase 3: score a model's own six open PDF answers with its abstract."""
import datetime as dt
import os
import sqlite3
import subprocess
import time

from huggingface_hub import hf_hub_download
from llama_cpp import Llama, LlamaGrammar

from review_arxiv_pdfs import MODELS, ROOT
from phase3_policy import POLICIES, grammar_for, parse_response
from pdf_weekday import DAY_GROUPS, qwen_pdf_db

KEY = os.environ["PDF_SCORE_MODEL"]
POLICY = os.environ.get("PDF_SCORE_POLICY", "standard")
if POLICY not in POLICIES or (KEY != "qwen3_8b" and POLICY != "standard"):
    raise ValueError(f"Unsupported phase 3 model/policy: {KEY}/{POLICY}")
SOURCE_KEY = {"qwen3_8b": "qwen3_rag_8b", "openscholar_8b": "openscholar_rag_8b"}[KEY]
REPO, FILE, LABEL = MODELS[KEY]
SOURCES = ([qwen_pdf_db(ROOT, group) for group in DAY_GROUPS] if KEY == "qwen3_8b" else
           [ROOT / "abstract_evaluation" / "open_answers_v1" / f"pdf_answers_{SOURCE_KEY}.sqlite"])
DB = ROOT / "abstract_evaluation" / "phase3" / (
    f"pdf_score_{KEY}_all_no_u.sqlite" if POLICY == "all_no_u" else f"pdf_score_{KEY}.sqlite")
GRAMMAR = LlamaGrammar.from_string(grammar_for(POLICY), verbose=False)


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id TEXT PRIMARY KEY, modelo TEXT NOT NULL, version_rubrica INTEGER NOT NULL CHECK(version_rubrica=1),
        intentos INTEGER NOT NULL DEFAULT 0 CHECK(intentos IN (-1,0,1,2,3)),
        source_model TEXT, puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000),
        Q1 TEXT, Q2 TEXT, Q3 TEXT, Q4 TEXT, Q5 TEXT, Q6 TEXT,
        raw_response TEXT, completed_at_utc TEXT)""")
    columns = [row[1] for row in db.execute("PRAGMA table_info(evaluations)")]
    if "intentos" not in columns:
        db.execute("ALTER TABLE evaluations ADD COLUMN intentos INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE evaluations SET intentos=0")
    schema = db.execute("SELECT sql FROM sqlite_master WHERE name='evaluations'").fetchone()[0]
    if "source_model TEXT NOT NULL" in schema:
        db.execute("""CREATE TABLE evaluations_new (
            id TEXT PRIMARY KEY, modelo TEXT NOT NULL,
            version_rubrica INTEGER NOT NULL CHECK(version_rubrica=1),
            intentos INTEGER NOT NULL DEFAULT 0 CHECK(intentos IN (-1,0,1,2,3)),
            source_model TEXT, puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000),
            Q1 TEXT, Q2 TEXT, Q3 TEXT, Q4 TEXT, Q5 TEXT, Q6 TEXT,
            raw_response TEXT, completed_at_utc TEXT)""")
        db.execute("""INSERT INTO evaluations_new
            (id,modelo,version_rubrica,intentos,source_model,puntuacion,
             Q1,Q2,Q3,Q4,Q5,Q6,raw_response,completed_at_utc)
            SELECT id,modelo,version_rubrica,0,source_model,puntuacion,
                   Q1,Q2,Q3,Q4,Q5,Q6,raw_response,completed_at_utc
            FROM evaluations""")
        db.execute("DROP TABLE evaluations")
        db.execute("ALTER TABLE evaluations_new RENAME TO evaluations")
    db.commit()
    return db


def papers(db):
    done = {row[0] for row in db.execute("SELECT id FROM evaluations WHERE intentos IN (0,-1)")}
    metadata = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        if path.name.endswith(".coverage.sqlite"):
            continue
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as library:
            for aid, abstract, stamp in library.execute(
                "SELECT arxiv_id,summary,COALESCE(NULLIF(updated,''),published) FROM papers"):
                if aid not in metadata or stamp > metadata[aid][1]:
                    metadata[aid] = (abstract, stamp, path.stem.replace("_", "."))
    pending = []
    seen = set()
    for path in SOURCES:
        if not path.exists():
            continue
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as source:
            for row in source.execute("SELECT id,modelo,answer1,answer2,answer3,answer4,answer5,answer6 FROM answers WHERE intentos=0"):
                if row[0] in done or row[0] in seen or row[0] not in metadata or not metadata[row[0]][0].strip():
                    continue
                seen.add(row[0])
                pending.append((metadata[row[0]][1], row[0], metadata[row[0]][2],
                                metadata[row[0]][0], row[1], row[2:]))
    return sorted(pending, reverse=True)


def push_result(db):
    db.close()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", DB.relative_to(ROOT).as_posix()], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"Save phase 3 PDF score {KEY}"], cwd=ROOT, check=True)
    for attempt in range(1, 9):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db()
        time.sleep(min(15, 3 * attempt))
    raise RuntimeError("Could not push phase 3 score")


def main():
    deadline = int(os.environ.get("PDF_SCORE_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    db = open_db()
    pending = papers(db)
    print(f"{LABEL}: {len(pending)} phase 3 papers pending", flush=True)
    if not pending:
        db.close()
        return
    prompt_name = "pdf_answers_score_prompt.txt" if POLICY == "standard" else f"pdf_answers_score_prompt_{POLICY}.txt"
    prompt = (ROOT / "scripts" / prompt_name).read_text(encoding="utf-8")
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=8192, n_threads=min(4, os.cpu_count() or 1),
                  n_batch=256, verbose=False)
    last_push = time.monotonic()
    unsaved = 0
    for _, aid, category, abstract, source_model, answers in pending:
        if time.time() >= deadline:
            break
        started = time.monotonic()
        prior = db.execute("SELECT intentos FROM evaluations WHERE id=?", (aid,)).fetchone()
        attempt = min(3, (prior[0] if prior and prior[0] > 0 else 0) + 1)
        db.execute("""INSERT INTO evaluations (id,modelo,version_rubrica,intentos)
            VALUES (?,?,1,?) ON CONFLICT(id) DO UPDATE SET intentos=excluded.intentos""",
            (aid, LABEL, attempt))
        db.commit()
        unsaved += 1
        try:
            body = "<ABSTRACT_AUTHOR_PROMISE>\n" + abstract + "\n</ABSTRACT_AUTHOR_PROMISE>\n"
            body += "<PDF_ANSWERS_ACCEPTED_AS_FACTS>\n"
            body += "\n".join(f"{i}. {answer}" for i, answer in enumerate(answers, 1))
            body += "\n</PDF_ANSWERS_ACCEPTED_AS_FACTS>"
            request = prompt + "\n\n" + body
            if len(model.tokenize(request.encode("utf-8"))) + 128 > 8192:
                raise ValueError("Abstract and answers exceed model context")
            result = model.create_chat_completion(
                messages=[{"role": "user", "content": request}], temperature=0,
                max_tokens=128, grammar=GRAMMAR)
            raw = result["choices"][0]["message"]["content"] or ""
            choices, score = parse_response(raw, POLICY)
            db.execute("""UPDATE evaluations SET intentos=0,source_model=?,puntuacion=?,
                Q1=?,Q2=?,Q3=?,Q4=?,Q5=?,Q6=?,raw_response=?,completed_at_utc=? WHERE id=?""",
                (source_model, score, *choices, raw, dt.datetime.now(dt.timezone.utc).isoformat(), aid))
            db.commit()
            print(f"{aid} {category} attempt={attempt} duration_s={time.monotonic()-started:.1f} "
                  f"score={score} error=None", flush=True)
        except Exception as exc:
            if attempt == 3:
                db.execute("UPDATE evaluations SET intentos=-1 WHERE id=?", (aid,))
                db.commit()
            print(f"{aid} {category} attempt={attempt} duration_s={time.monotonic()-started:.1f} "
                  f"score=None error={type(exc).__name__}: {exc}", flush=True)
        if unsaved and time.monotonic() - last_push >= 30 * 60:
            print(f"Saving {unsaved} phase 3 evaluations to GitHub", flush=True)
            db = push_result(db)
            unsaved = 0
            last_push = time.monotonic()
    if unsaved:
        print(f"Saving final {unsaved} phase 3 evaluations to GitHub", flush=True)
        db = push_result(db)
    db.close()


if __name__ == "__main__":
    main()
