"""Answer six open scientific questions from arXiv PDF text."""
import datetime as dt
import gc
import json
import os
import re
import sqlite3
import subprocess
import time

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from review_arxiv_pdfs import MODELS, ROOT, fetch_pdf, page_text

KEY = os.environ.get("PDF_REVIEW_MODEL", "qwen3_8b")
REPO, FILE, LABEL = MODELS[KEY]
RAG = KEY.endswith("_rag_8b")
DB = ROOT / "abstract_evaluation" / "open_answers_v1" / f"pdf_answers_{KEY}.sqlite"
CONTEXT_TOKENS = 16384
OUTPUT_TOKENS = 3500


def open_db():
    db = sqlite3.connect(DB, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    return db


def candidates(db):
    done = {row[0] for row in db.execute("SELECT id FROM answers")}
    qualified = set()
    for path in sorted((ROOT / "abstract_evaluation").glob("rubric_v3_qwen3_8b*.sqlite")):
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as source:
            for aid, score, q2, q3 in source.execute("SELECT id,puntuacion,Q2,Q3 FROM evaluations"):
                if score is not None and (score > 800 or (700 < score <= 800 and q2 == "D" and q3 in ("C", "D"))):
                    qualified.add(aid)
    found = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as source:
            for aid, stamp, links in source.execute(
                "SELECT arxiv_id,COALESCE(NULLIF(updated,''),published),links_json FROM papers"
            ):
                if aid in done or aid not in qualified:
                    continue
                for link in json.loads(links):
                    url = link.get("href", "")
                    if link.get("type") == "application/pdf" and url.startswith("https://arxiv.org/pdf/"):
                        if aid not in found or stamp > found[aid][0]:
                            found[aid] = (stamp, url)
                        break
    return sorted(((stamp, aid, url) for aid, (stamp, url) in found.items()), reverse=True)


def supplied_text(pages):
    if not RAG:
        return "\n\n".join(pages)
    from pdf_local_rag import QUESTIONS, build_chunks, retrieve

    chunks = build_chunks(pages)
    selected = {}
    for query in QUESTIONS.values():
        for page, body in retrieve(chunks, query, count=2):
            selected[(page, body)] = True
    if not selected:
        raise ValueError("No relevant PDF passages retrieved")
    return "\n\n".join(f"[PDF page {page}]\n{body}" for page, body in selected)


def split_answers(raw):
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1]
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = "\n".join(raw.splitlines()[1:-1]).strip()
    matches = list(re.finditer(r"(?m)^\s*([1-6])\s*[.):]\s*", raw))
    if len(matches) != 6 or [int(match.group(1)) for match in matches] != list(range(1, 7)):
        raise ValueError("Expected six numbered answers in order")
    answers = [raw[match.end():matches[i + 1].start() if i < 5 else len(raw)].strip()
               for i, match in enumerate(matches)]
    if any(not answer for answer in answers):
        raise ValueError("One or more answers are empty")
    return answers


def push_result(db):
    db.close()
    rel = DB.relative_to(ROOT).as_posix()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", rel], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db()
    subprocess.run(["git", "commit", "-m", f"Save open PDF answers {KEY}"], cwd=ROOT, check=True)
    for attempt in range(1, 5):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db()
        time.sleep(3 * attempt)
    raise RuntimeError("Could not push PDF answers")


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    db = open_db()
    papers = candidates(db)
    print(f"{LABEL}: {len(papers)} papers pending; open answers v1", flush=True)
    if not papers:
        db.close()
        return
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=CONTEXT_TOKENS,
                  n_threads=min(4, os.cpu_count() or 1), n_batch=256, verbose=False)
    prompt_file = "pdf_open_questions_rag.txt" if RAG else "pdf_open_questions_full.txt"
    instructions = (ROOT / "scripts" / prompt_file).read_text(encoding="utf-8-sig")
    for _, aid, url in papers:
        if time.time() >= deadline:
            break
        started = time.monotonic()
        try:
            pages = page_text(fetch_pdf(url))
            body = supplied_text(pages)
            request = instructions + "\n\n<PAPER_TEXT>\n" + body + "\n</PAPER_TEXT>"
            token_count = len(model.tokenize(request.encode("utf-8")))
            if token_count + OUTPUT_TOKENS + 64 > CONTEXT_TOKENS:
                raise ValueError(f"PDF text exceeds model context budget: {token_count} input tokens")
            result = model.create_chat_completion(
                messages=[{"role": "user", "content": request}], temperature=0.1,
                max_tokens=OUTPUT_TOKENS,
            )
            raw = result["choices"][0]["message"]["content"] or ""
            answers = split_answers(raw)
            db.execute("""INSERT OR IGNORE INTO answers
                (id,modelo,version_preguntas,pdf_url,modalidad,answer1,answer2,answer3,
                 answer4,answer5,answer6,raw_response,completed_at_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (aid, LABEL, 1, url, "rag" if RAG else "full_text", *answers, raw,
                 dt.datetime.now(dt.timezone.utc).isoformat()))
            db.commit()
            print(f"{aid} duration_s={time.monotonic()-started:.1f} status=valid", flush=True)
            db = push_result(db)
        except Exception as exc:
            print(f"{aid} duration_s={time.monotonic()-started:.1f} status=error "
                  f"error={type(exc).__name__}: {exc}", flush=True)
        finally:
            gc.collect()
    db.close()


if __name__ == "__main__":
    main()
