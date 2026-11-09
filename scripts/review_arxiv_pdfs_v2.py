"""Phase-two rubric v2: complete extracted PDF text, except the RAG pilot."""
import gc
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from pdf_review_v2_storage import normalize, open_db, save
from review_arxiv_pdfs import MODELS, ROOT, fetch_pdf, page_text

KEY = os.environ.get("PDF_REVIEW_MODEL", "qwen3_8b")
REPO, FILE, LABEL = MODELS[KEY]
RUBRIC_VERSION = 3 if KEY == "openscholar_rag_8b" else 2
DB = ROOT / "abstract_evaluation" / f"v{RUBRIC_VERSION}" / f"pdf_review_{KEY}.sqlite"
CONTEXT_TOKENS = 16384
OUTPUT_TOKENS = 2000


def candidates(db):
    done = {row[0] for row in db.execute("SELECT id FROM evaluations")}
    qualified = set()
    for path in sorted((ROOT / "abstract_evaluation").glob("rubric_v3_qwen3_8b*.sqlite")):
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for aid, score, q2, q3 in source.execute("SELECT id,puntuacion,Q2,Q3 FROM evaluations"):
                if score is not None and (score > 800 or (700 < score <= 800 and q2 == "D" and q3 in ("C", "D"))):
                    qualified.add(aid)
        finally:
            source.close()
    found = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for aid, stamp, links in source.execute(
                "SELECT arxiv_id,COALESCE(NULLIF(updated,''),published),links_json FROM papers"
            ):
                if aid in done or aid not in qualified:
                    continue
                for link in json.loads(links):
                    if link.get("type") == "application/pdf" and link.get("href", "").startswith("https://arxiv.org/pdf/"):
                        if aid not in found or stamp > found[aid][0]:
                            found[aid] = (stamp, link["href"])
                        break
        finally:
            source.close()
    return sorted(((stamp, aid, url) for aid, (stamp, url) in found.items()), reverse=True)


def content_for_review(pages, instructions):
    if KEY == "openscholar_rag_8b":
        from pdf_local_rag import QUESTIONS, build_chunks, retrieve

        chunks = build_chunks(pages)
        selected = {}
        for query in QUESTIONS.values():
            for page, body in retrieve(chunks, query, count=2):
                selected[(page, body)] = True
        if not selected:
            raise ValueError("No relevant PDF text retrieved")
        instructions = instructions.replace(
            "You are given the complete text extracted from a scientific paper's PDF.\n"
            'In these instructions, "the paper" means that supplied text.',
            "You are given selected verbatim passages extracted from one scientific paper's PDF.\n"
            'In these instructions, "the paper" means only the supplied passages. '
            "They may omit decisive evidence; choose U when necessary.",
        )
        body = "\n\n".join(f"[PDF page {page}]\n{text}" for page, text in selected)
    else:
        body = "\n\n".join(pages)
    return instructions + "\n\n<PAPER_TEXT>\n" + body + "\n</PAPER_TEXT>", body


def response_text(raw):
    # Remove only transport wrappers; structural validation remains strict.
    raw = raw.strip()
    if "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1])
    return raw


def push_result(db):
    db.close()
    rel = DB.relative_to(ROOT).as_posix()
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", rel], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db(DB, RUBRIC_VERSION)
    subprocess.run(["git", "commit", "-m", f"Save PDF rubric v{RUBRIC_VERSION} {KEY}"], cwd=ROOT, check=True)
    for attempt in range(1, 5):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db(DB, RUBRIC_VERSION)
        time.sleep(3 * attempt)
    raise RuntimeError("Could not push valid PDF review")


def main():
    deadline = int(os.environ.get("PDF_REVIEW_DEADLINE_EPOCH", str(int(time.time()) + 345 * 60)))
    limit = int(os.environ.get("PDF_REVIEW_MAX_PAPERS", "0"))
    db = open_db(DB, RUBRIC_VERSION)
    papers = candidates(db)
    print(f"{LABEL}: {len(papers)} papers pending; rubric v{RUBRIC_VERSION}", flush=True)
    if not papers:
        db.close()
        return
    model_path = hf_hub_download(repo_id=REPO, filename=FILE)
    model = Llama(model_path=model_path, n_ctx=CONTEXT_TOKENS, n_threads=min(4, os.cpu_count() or 1),
                  n_batch=256, verbose=False)
    prompt_file = "pdf_review_prompt_v3.txt" if RUBRIC_VERSION == 3 else "pdf_review_prompt.txt"
    instructions = (ROOT / "scripts" / prompt_file).read_text(encoding="utf-8-sig")
    processed = 0
    for _, aid, url in papers:
        if time.time() >= deadline or (limit and processed >= limit):
            break
        processed += 1
        started = time.monotonic()
        data = pages = None
        try:
            data = fetch_pdf(url)
            pages = page_text(data)
            request, supplied_text = content_for_review(pages, instructions)
            token_count = len(model.tokenize(request.encode("utf-8")))
            if token_count + OUTPUT_TOKENS + 64 > CONTEXT_TOKENS:
                raise ValueError(f"PDF text exceeds model context budget: {token_count} input tokens")
            result = model.create_chat_completion(
                messages=[{"role": "user", "content": request}],
                temperature=0.1, max_tokens=OUTPUT_TOKENS,
            )
            raw = result["choices"][0]["message"]["content"] or ""
            if KEY == "openscholar_rag_8b":
                print(f"--- MODEL RESPONSE BEGIN {aid} ---\n{raw}\n--- MODEL RESPONSE END {aid} ---", flush=True)
            values = normalize(response_text(raw), supplied_text,
                               lenient_trace=(KEY == "openscholar_rag_8b"))
            if KEY == "openscholar_rag_8b":
                print(f"{aid} trace_validation=skipped_for_E_and_J", flush=True)
            score = save(db, aid, LABEL, url, values, RUBRIC_VERSION)
            duration = time.monotonic() - started
            print(f"{aid} attempt=1 duration_s={duration:.1f} score={score} status=valid error=None", flush=True)
        except Exception as exc:
            duration = time.monotonic() - started
            print(f"{aid} attempt=1 duration_s={duration:.1f} score=None status=error "
                  f"error={type(exc).__name__}: {exc}", flush=True)
        finally:
            del data, pages
            gc.collect()
        if 'values' in locals() and 'score' in locals():
            db = push_result(db)
            del values, score
    db.close()


if __name__ == "__main__":
    main()
