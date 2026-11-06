#!/usr/bin/env python3
"""Evaluate the version 3 rubric from abstracts with literal evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from collections import defaultdict, deque
from itertools import chain
from pathlib import Path

from huggingface_hub import hf_hub_download
from llama_cpp import Llama, LlamaGrammar

from rubric_v3_storage import open_db, save_response

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEY = os.environ.get("RUBRIC_MODEL", "qwen3_8b")
MODELS = {
    "qwen3_8b": ("Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B Q4_K_M"),
    "openscholar_8b": ("QuantFactory/Llama-3.1_OpenScholar-8B-GGUF", "Llama-3.1_OpenScholar-8B.Q4_K_M.gguf", "OpenScholar-8B Q4_K_M"),
}
if MODEL_KEY not in MODELS:
    raise ValueError("Unknown model")
MODEL_REPO, MODEL_FILE, MODEL_LABEL = MODELS[MODEL_KEY]
DB_PATH = ROOT / "abstract_evaluation" / f"rubric_v3_{MODEL_KEY}.sqlite"

# The grammar enforces seven ordered choices. The parser independently verifies it.
GRAMMAR = 'root ::= "T=" type "\\nQ1=" choice "\\nE1=\\\"" txt "\\\"\\nJ1=\\\"" txt "\\\"\\nQ2=" choice "\\nE2=\\\"" txt "\\\"\\nJ2=\\\"" txt "\\\"\\nQ3=" choice "\\nE3=\\\"" txt "\\\"\\nJ3=\\\"" txt "\\\"\\nQ4=" choice "\\nE4=\\\"" txt "\\\"\\nJ4=\\\"" txt "\\\""\nchoice ::= [AaBbCcDdUu]\ntype ::= [TtEeMmSsPpUu]\ntxt ::= [^"\\n]*\n'

RUBRIC = """Evaluate the scientific importance of the actual contribution reported in this abstract. For each criterion choose ONE letter. Judge only the abstract. A, B, C and D are ordered categories; U means the abstract lacks enough information. Do not calculate a score.

I1 — Importance of the scientific question:
A NARROW: localized technical question with limited consequences.
B SPECIALIZED: relevant question within one research area.
C MAJOR: important discipline problem or significant physical phenomenon.
D FUNDAMENTAL: central question about physical laws, principles, constituents, or nature.
Judge the question separately from the magnitude of this paper's result.

I2 — Novelty of the reported contribution relative to prior knowledge described here:
A REPLICATIVE: reproduces, confirms, or applies established knowledge without a substantial new knowledge contribution.
B INCREMENTAL: improves precision, scope, coverage, or formulation.
C DISTINCT: clearly different result, method, phenomenon, or explanation; also a confirmation establishing previously unconfirmed knowledge.
D RADICALLY_NEW: fundamentally new principle, phenomenon, or framework with a concrete distinction.

I3 — Magnitude of progress actually reported:
A MINIMAL: no substantive result, or only a proposal.
B LIMITED: partial result with limited consequences.
C SUBSTANTIAL: important result advancing understanding or reducing meaningful uncertainty.
D DECISIVE: resolves an important question, establishes a fundamental discovery, or decisively discriminates within its stated scope.
A rigorous theoretical derivation can be decisive without experimental confirmation.

I4 — Scope of implications supported by the result:
A LOCALIZED: confined to a specific system or narrow configuration.
B SPECIALIZED: applies to a family of problems in one specialization.
C BROAD: relevant to several important problems or research directions.
D CROSS_FIELD: implications across disciplines or generally applicable physical principles.
Judge generality of implications, not the number of objects or datasets.

I5 — Conceptual consequences:
A UNCHANGED: no identifiable change to understanding.
B REFINED: improves an established explanation, model, or framework.
C EXTENDED: substantially expands what can be explained or understood.
D FOUNDATIONAL: establishes or challenges a fundamental principle or assumption.

I6 — Explanatory, predictive, or discriminating power:
A NONE: no identifiable improvement.
B LIMITED: partial improvement.
C SUBSTANTIAL: explains phenomena, makes specific predictions, or significantly constrains hypotheses.
D DECISIVE: resolves an important ambiguity or decisively distinguishes hypotheses.

I7 — Enabling scientific capability:
A ROUTINE: no significant new research capability.
B IMPROVING: improves existing investigation.
C ENABLING: makes difficult investigations, calculations, measurements, or scales accessible.
D TRANSFORMATIVE: qualitatively new capability with clear important applications.

Return exactly these seven lines, replacing each ? with A, B, C, D, or U:
I1=?
I2=?
I3=?
I4=?
I5=?
I6=?
I7=?
"""


def prompt(abstract: str) -> str:
    return (ROOT / "scripts" / "rubric_v3_prompt.txt").read_text(encoding="utf-8") + "\n<abstract>\n" + abstract.strip()[:10000] + "\n</abstract>"


def collect_candidates(db: sqlite3.Connection):
    categories = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["categories"]
    done = {row[0] for row in db.execute("SELECT arxiv_id FROM evaluations WHERE validation_error IS NULL")}
    pending = {}
    for category in categories:
        path = ROOT / "library" / f"{category.replace('.', '_')}.sqlite"
        if not path.exists():
            continue
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for arxiv_id, abstract, timestamp in source.execute("""
                SELECT arxiv_id,summary,COALESCE(NULLIF(updated,''),published)
                FROM papers WHERE TRIM(COALESCE(summary,''))<>''
                AND json_extract(primary_category_json,'$.term')=?
                ORDER BY COALESCE(NULLIF(updated,''),published) DESC,arxiv_id DESC
            """, (category,)):
                if arxiv_id not in done and arxiv_id not in pending:
                    pending[arxiv_id] = (arxiv_id, abstract, timestamp, category)
        finally:
            source.close()
    return list(pending.values()), categories


def ordered_candidates(papers, categories):
    priority_name = "priority_mod0.json" if MODEL_KEY == "qwen3_8b" else "priority_mod1.json"
    path = ROOT / "abstract_evaluation" / priority_name
    requests = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not isinstance(requests, list):
        raise ValueError("Priority file must be a JSON array")
    by_id = {paper[0]: paper for paper in papers}
    seen = set()
    priorities = []
    for request in sorted((r for r in requests if isinstance(r, dict)), key=lambda r: r.get("requested_at_utc", "")):
        arxiv_id = request.get("arxiv_id")
        if arxiv_id in by_id and arxiv_id not in seen:
            priorities.append(by_id[arxiv_id])
            seen.add(arxiv_id)
    days = defaultdict(lambda: defaultdict(deque))
    for paper in papers:
        if paper[0] not in seen:
            days[paper[2][:10]][paper[3]].append(paper)
    def regular():
        for day in sorted(days, reverse=True):
            queues = days[day]
            for category in categories:
                queues[category] = deque(sorted(queues[category], key=lambda p: (p[2],p[0]), reverse=True))
            while any(queues.values()):
                for category in categories:
                    if queues[category]:
                        yield queues[category].popleft()
    return chain(priorities, regular())


def infer(model: Llama, grammar: LlamaGrammar, abstract: str, seed: int):
    message = prompt(abstract)
    if MODEL_KEY == "qwen3_8b":
        message = "/no_think\n" + message
    started = time.monotonic()
    output = model.create_chat_completion(
        messages=[{"role": "user", "content": message}],
        grammar=grammar, temperature=0.2, top_p=0.8, seed=seed,
        max_tokens=650,
    )
    raw = output["choices"][0]["message"]["content"] or ""
    usage = output.get("usage") or {}
    return raw, int((time.monotonic() - started)*1000), usage.get("prompt_tokens"), usage.get("completion_tokens")


def checkpoint(db: sqlite3.Connection):
    db.close()
    relative = DB_PATH.relative_to(ROOT).as_posix()
    subprocess.run(["git", "add", "--", relative], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return open_db(DB_PATH)
    subprocess.run(["git", "-c", "user.name=github-actions[bot]", "-c",
                    "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                    "commit", "-m", f"Guardar rúbrica v3 {MODEL_KEY}"], cwd=ROOT, check=True)
    for attempt in range(3):
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=ROOT, check=True)
        if subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=ROOT).returncode == 0:
            return open_db(DB_PATH)
        time.sleep(3*(attempt+1))
    raise RuntimeError("Could not publish SQLite checkpoint")


def main():
    deadline = int(os.environ.get("IMPORTANCE_DEADLINE_EPOCH", str(int(time.time())+345*60)))
    max_papers = int(os.environ.get("RUBRIC_MAX_PAPERS", "0") or "0")
    if max_papers < 0:
        raise ValueError("RUBRIC_MAX_PAPERS cannot be negative")
    db = open_db(DB_PATH)
    try:
        papers, categories = collect_candidates(db)
        print(f"{MODEL_LABEL}: {len(papers)} papers pending", flush=True)
        if not papers:
            return
        model_file = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
        model = Llama(model_path=model_file, n_ctx=8192, n_threads=min(4,os.cpu_count() or 1),
                      n_batch=256, verbose=False)
        grammar = LlamaGrammar.from_string(GRAMMAR, verbose=False)
        next_checkpoint = time.monotonic()+30*60
        valid = invalid = processed = consecutive_failures = 0
        for arxiv_id, abstract, timestamp, category in ordered_candidates(papers,categories):
            if time.time() >= deadline or (max_papers and processed >= max_papers):
                break
            processed += 1
            paper_valid = False
            for attempt in (1,2):
                if time.time() >= deadline:
                    break
                try:
                    raw,latency,input_tokens,output_tokens = infer(model,grammar,abstract,17+attempt-1)
                except Exception as error:
                    raw,latency,input_tokens,output_tokens = f"INFERENCE_ERROR: {type(error).__name__}: {error}",None,None,None
                record_id = save_response(db,arxiv_id=arxiv_id,model=MODEL_LABEL,abstract=abstract,
                    raw=raw,latency_ms=latency,input_tokens=input_tokens,output_tokens=output_tokens)
                score,error = db.execute("SELECT puntuacion_centesimas,validation_error FROM evaluations WHERE id=?",(record_id,)).fetchone()
                print(f"{arxiv_id} {category} attempt={attempt} score={score} error={error}",flush=True)
                if error is None:
                    valid += 1
                    paper_valid = True
                    break
                invalid += 1
            consecutive_failures = 0 if paper_valid else consecutive_failures + 1
            if time.monotonic() >= next_checkpoint:
                db=checkpoint(db)
                next_checkpoint=time.monotonic()+30*60
            if consecutive_failures >= 3:
                raise RuntimeError("Three papers produced no valid rubric choices; stopping early")
        print(f"Completed: {valid} valid papers, {invalid} invalid attempts",flush=True)
        if processed and valid == 0:
            raise RuntimeError("No valid rubric choices saved")
    finally:
        db.close()


if __name__ == "__main__":
    main()
