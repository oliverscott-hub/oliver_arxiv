"""Phase 3: score a model's own six open PDF answers with its abstract."""
import datetime as dt
import os
import re
import sqlite3
import subprocess
import time

from huggingface_hub import hf_hub_download
from llama_cpp import Llama, LlamaGrammar

from review_arxiv_pdfs import MODELS, ROOT

KEY = os.environ["PDF_SCORE_MODEL"]
SOURCE_KEY = {"qwen3_8b": "qwen3_rag_8b", "openscholar_8b": "openscholar_rag_8b"}[KEY]
REPO, FILE, LABEL = MODELS[KEY]
SOURCE = ROOT / "abstract_evaluation" / "open_answers_v1" / f"pdf_answers_{SOURCE_KEY}.sqlite"
DB = ROOT / "abstract_evaluation" / "phase3" / f"pdf_score_{KEY}.sqlite"
WEIGHTS = (15, 30, 15, 10, 20, 10)
GRAMMAR = LlamaGrammar.from_string(
    'root ::= "Q1=" choice "\\nQ2=" choice "\\nQ3=" choice "\\nQ4=" choice "\\nQ5=" choice "\\nQ6=" choice\n'
    'choice ::= [ABCDU]\n', verbose=False)


def open_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id TEXT PRIMARY KEY, modelo TEXT NOT NULL, version_rubrica INTEGER NOT NULL CHECK(version_rubrica=1),
        source_model TEXT NOT NULL, puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000),
        Q1 TEXT NOT NULL, Q2 TEXT NOT NULL, Q3 TEXT NOT NULL,
        Q4 TEXT NOT NULL, Q5 TEXT NOT NULL, Q6 TEXT NOT NULL,
        raw_response TEXT NOT NULL, completed_at_utc TEXT NOT NULL)""")
    db.commit()
    return db


def papers(db):
    done = {row[0] for row in db.execute("SELECT id FROM evaluations")}
    metadata = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as library:
            for aid, abstract, stamp in library.execute(
                "SELECT arxiv_id,summary,COALESCE(NULLIF(updated,''),published) FROM papers"):
                if aid not in metadata or stamp > metadata[aid][1]:
                    metadata[aid] = (abstract, stamp, path.stem.replace("_", "."))
    with sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True) as source:
        rows = source.execute("SELECT id,modelo,answer1,answer2,answer3,answer4,answer5,answer6 FROM answers")
        pending = [(metadata[row[0]][1], row[0], metadata[row[0]][2], metadata[row[0]][0], row[1], row[2:])
                   for row in rows if row[0] not in done and row[0] in metadata and metadata[row[0]][0].strip()]
    return sorted(pending, reverse=True)


def parse(raw):
    matches = re.fullmatch(r"Q1=([ABCDU])\nQ2=([ABCDU])\nQ3=([ABCDU])\nQ4=([ABCDU])\nQ5=([ABCDU])\nQ6=([ABCDU])", raw.strip())
    if not matches:
        raise ValueError("Expected six single-letter Q answers")
    choices = matches.groups()
    score = None
    if "U" not in choices:
        weighted = sum(weight * "ABCD".index(choice) for weight, choice in zip(WEIGHTS, choices))
        score = (weighted * 10 + 1) // 3
    return choices, score


def push_result(db):
    db.close()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", DB.relative_to(ROOT).as_posix()], cwd=ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"Save phase 3 PDF score {KEY}"], cwd=ROOT, check=True)
    for attempt in range(1, 5):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db()
        time.sleep(3 * attempt)
    raise RuntimeError("Could not push phase 3 score")


def main():
    deadline = int(os.environ.get("PDF_SCORE_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    db = open_db()
    pending = papers(db)
    print(f"{LABEL}: {len(pending)} phase 3 papers pending", flush=True)
    if not pending:
        db.close()
        return
    prompt = (ROOT / "scripts" / "pdf_answers_score_prompt.txt").read_text(encoding="utf-8")
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=8192, n_threads=min(4, os.cpu_count() or 1),
                  n_batch=256, verbose=False)
    for _, aid, category, abstract, source_model, answers in pending:
        if time.time() >= deadline:
            break
        started = time.monotonic()
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
            choices, score = parse(raw)
            db.execute("""INSERT OR IGNORE INTO evaluations
                (id,modelo,version_rubrica,source_model,puntuacion,Q1,Q2,Q3,Q4,Q5,Q6,raw_response,completed_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (aid, LABEL, 1, source_model, score, *choices, raw,
                 dt.datetime.now(dt.timezone.utc).isoformat()))
            db.commit()
            print(f"{aid} {category} attempt=1 duration_s={time.monotonic()-started:.1f} "
                  f"score={score} error=None", flush=True)
            db = push_result(db)
        except Exception as exc:
            print(f"{aid} {category} attempt=1 duration_s={time.monotonic()-started:.1f} "
                  f"score=None error={type(exc).__name__}: {exc}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
