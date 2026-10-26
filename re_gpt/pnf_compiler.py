"""Production PNF Fleet Compiler bridging ChatGPT research threads to slr's world compiler.

Compiles archived ChatGPT conversation graphs into canonical observation streams (.slro),
executes the native Rust sensiblaw-world-compiler release binary, and produces the
durable binary world store (.slrw) with exact PNF candidates, world atoms, and iteration receipts.
"""

from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import subprocess
import time
from typing import Any, Mapping, Sequence

from .storage import DEFAULT_DB_PATH

SLR_COMPILER_RELEASE_BIN = Path("/home/c/Documents/code/slr/target/release/sensiblaw-world-compiler")
DEFAULT_WORLD_DIR = Path.home() / ".chatgpt_fleet_world"

# DependencyShape mapping matching sl-world-compiler DependencyShape
SPACY_DEP_TO_SHAPE: dict[str, int] = {
    "nsubj": 1,        # NominalSubject
    "dobj": 2,         # DirectObject
    "obj": 2,          # DirectObject
    "nsubjpass": 3,    # PassiveSubject
    "amod": 4,         # AdjectivalModifier
    "nmod": 5,         # NominalModifier
    "compound": 5,     # NominalModifier
    "conj": 6,         # Conjunction
    "neg": 7,          # Negation
    "aux": 8,          # ModalAuxiliary
    "auxpass": 8,      # ModalAuxiliary
    "det": 9,          # Determiner
    "npadvmod": 10,    # TemporalModifier
    "tmod": 10,        # TemporalModifier
    "advmod": 10,      # TemporalModifier
    "ccomp": 11,       # ClausalComplement
    "xcomp": 12,       # OpenClausalComplement
    "advcl": 13,       # AdverbialClause
    "acl": 14,         # ClausalModifier
    "relcl": 15,       # RelativeClause
}

SHAPE_NAMES: dict[int, str] = {
    1: "NominalSubject",
    2: "DirectObject",
    3: "PassiveSubject",
    4: "AdjectivalModifier",
    5: "NominalModifier",
    6: "Conjunction",
    7: "Negation",
    8: "ModalAuxiliary",
    9: "Determiner",
    10: "TemporalModifier",
    11: "ClausalComplement",
    12: "OpenClausalComplement",
    13: "AdverbialClause",
    14: "ClausalModifier",
    15: "RelativeClause",
    16: "UnresolvedDependency",
}

FRAGMENT_NAMES: dict[int, str] = {
    1: "actor",
    2: "patient",
    3: "property",
    4: "relation",
    5: "conjunction",
    6: "negation",
    7: "modality",
    8: "quantifier",
    9: "temporal",
    10: "content_clause",
    11: "clause_attachment",
    12: "unresolved",
}


def write_text(buf: bytearray, s: str) -> None:
    b = s.encode("utf-8")
    buf.extend(struct.pack("<I", len(b)))
    buf.extend(b)


class PnfFleetCompiler:
    """Compiles multi-thread ChatGPT history into slr PNF candidate and world atom records."""

    def __init__(
        self,
        *,
        db_path: Path | str = DEFAULT_DB_PATH,
        compiler_bin: Path | str = SLR_COMPILER_RELEASE_BIN,
        output_dir: Path | str = DEFAULT_WORLD_DIR,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.compiler_bin = Path(compiler_bin).expanduser()
        self.output_dir = Path(output_dir).expanduser()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def select_top_threads(self, limit: int = 200) -> list[dict[str, Any]]:
        """Select top hydrated conversations ordered by remote_update_time DESC."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.conversation_id, c.title, CAST(c.remote_update_time AS REAL) as ts, count(m.message_index) as msg_cnt
                FROM conversations c
                JOIN messages m ON c.conversation_id = m.conversation_id
                GROUP BY c.conversation_id
                ORDER BY ts DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()

            threads = []
            for cid, title, ts, cnt in rows:
                threads.append({
                    "conversation_id": cid,
                    "title": title or "(untitled)",
                    "remote_update_time": ts or 0.0,
                    "message_count": cnt,
                })
            return threads

    def compile_fleet(
        self,
        limit: int = 200,
        iteration: int = 1,
        progress_cb: Any = None,
    ) -> dict[str, Any]:
        """Execute full compilation of top threads through slr's release world compiler."""
        import spacy

        threads = self.select_top_threads(limit=limit)
        if not threads:
            raise ValueError(f"No hydrated conversations found in {self.db_path}")

        nlp = spacy.load("en_core_web_sm", disable=["ner"])
        nlp.max_length = 10_000_000

        slro_path = self.output_dir / f"fleet_top{limit}_iter{iteration}.slro"
        slrw_path = self.output_dir / f"fleet_top{limit}_iter{iteration}.slrw"

        buf = bytearray()
        total_tokens_encoded = 0
        total_sentences_encoded = 0
        thread_stats = []

        started_encode = time.perf_counter()

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            for i, th in enumerate(threads, 1):
                cid = th["conversation_id"]
                title = th["title"]
                ts = th["remote_update_time"]

                cur.execute(
                    "SELECT author, content FROM messages WHERE conversation_id = ? ORDER BY message_index ASC",
                    (cid,),
                )
                msgs = cur.fetchall()
                full_text = "\n\n".join(m[1] for m in msgs if m[1])
                h = hashlib.sha256(full_text.encode("utf-8")).digest()

                doc_ref = f"chat:{cid}"
                rev_ref = f"rev:{cid}:{int(ts)}"

                # 1. Manifestation record (kind = 1)
                buf.extend(b"SLRO")
                buf.extend(struct.pack("<H", 1))
                buf.append(1)  # kind 1
                write_text(buf, doc_ref)
                write_text(buf, "Q_CHAT")
                write_text(buf, "en")
                write_text(buf, rev_ref)
                buf.extend(h)

                # 2. Token observations (kind = 2)
                # PNF must cover the complete archived thread.  In particular, active
                # roadmap decisions often occur after long exploratory history.
                doc = nlp(full_text)

                conv_sentences = 0
                conv_tokens = 0

                for sid, sent in enumerate(doc.sents):
                    if not sent.text.strip():
                        continue
                    tok_ord = 0
                    for local, tok in enumerate(sent):
                        orth = tok.text.strip()
                        lemma = (tok.lemma_ or tok.text).strip()
                        if not orth or not lemma:
                            continue
                        head_local = (tok.head.i - sent.start) if tok.dep_ else tok_ord
                        if tok.is_punct or not any(c.isalnum() for c in orth):
                            shape = 16  # UnresolvedDependency -> parser debris / non-semantic
                        else:
                            shape = SPACY_DEP_TO_SHAPE.get(tok.dep_, 16)
                        head_lemma = (tok.head.lemma_ if tok.head else tok.text).strip() or lemma
                        head_orth = (tok.head.text if tok.head else tok.text).strip() or orth

                        buf.extend(b"SLRO")
                        buf.extend(struct.pack("<H", 1))
                        buf.append(2)  # Token
                        write_text(buf, doc_ref)
                        buf.extend(struct.pack("<Q", sid))
                        buf.extend(struct.pack("<I", tok_ord))
                        buf.extend(struct.pack("<I", tok.idx))
                        buf.extend(struct.pack("<I", tok.idx + len(tok.text)))
                        buf.extend(struct.pack("<I", head_local))
                        buf.append(shape)
                        write_text(buf, orth)
                        write_text(buf, lemma)
                        write_text(buf, head_orth)
                        write_text(buf, head_lemma)
                        tok_ord += 1
                        conv_tokens += 1
                    conv_sentences += 1

                total_tokens_encoded += conv_tokens
                total_sentences_encoded += conv_sentences
                thread_stats.append({
                    "conversation_id": cid,
                    "title": title,
                    "messages": len(msgs),
                    "sentences": conv_sentences,
                    "tokens": conv_tokens,
                })

                if progress_cb:
                    progress_cb(i, len(threads), cid, title)

        slro_path.write_bytes(buf)
        encode_duration_s = time.perf_counter() - started_encode

        # Invoke Rust sensiblaw-world-compiler
        started_compile = time.perf_counter()
        compile_cmd = [
            str(self.compiler_bin),
            "compile",
            "--iteration", str(iteration),
            "--input", str(slro_path),
            "--output", str(slrw_path),
        ]
        res = subprocess.run(compile_cmd, capture_output=True, text=True, check=False)
        compile_duration_s = time.perf_counter() - started_compile

        if res.returncode != 0:
            raise RuntimeError(
                f"sensiblaw-world-compiler failed with exit {res.returncode}:\n{res.stderr}\n{res.stdout}"
            )

        receipt_line = ""
        for line in res.stderr.splitlines():
            if "SLR_WORLD_COMPILER_BINARY_RECEIPT" in line:
                receipt_line = line
                break

        receipt_info = {}
        for token in receipt_line.split():
            if "=" in token:
                k, v = token.split("=", 1)
                if v in ("true", "false"):
                    receipt_info[k] = (v == "true")
                elif v.isdigit():
                    receipt_info[k] = int(v)
                else:
                    receipt_info[k] = v

        # Decode the .slrw world file to index PNF factors
        factors_by_thread, global_multiset = self._inspect_slrw_pnf(slrw_path)

        return {
            "threads_compiled": len(threads),
            "total_sentences": total_sentences_encoded,
            "total_tokens": total_tokens_encoded,
            "slro_path": str(slro_path),
            "slrw_path": str(slrw_path),
            "slro_bytes": len(buf),
            "slrw_bytes": slrw_path.stat().st_size,
            "encode_duration_s": round(encode_duration_s, 2),
            "compile_duration_s": round(compile_duration_s, 2),
            "compiler_receipt": receipt_info,
            "thread_stats": thread_stats,
            "unique_factors_count": len(global_multiset),
            "total_semantic_factors": sum(global_multiset.values()),
            "top_factors": [
                {"factor": f"{sig[0]}:{sig[1]}:{sig[2]}", "count": count}
                for sig, count in global_multiset.most_common(50)
            ],
            "factors_by_thread": factors_by_thread,
        }

    def _inspect_slrw_pnf(
        self, slrw_path: Path
    ) -> tuple[dict[str, list[dict[str, Any]]], collections.Counter]:
        """Decode .slrw world store binary records and collect PNF semantic factor multisets."""
        factors_by_thread: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        global_multiset: collections.Counter = collections.Counter()

        with open(slrw_path, "rb") as f:
            while True:
                magic = f.read(4)
                if not magic:
                    break
                if magic != b"SLRW":
                    break
                _ver = struct.unpack("<H", f.read(2))[0]
                header = f.read(2)
                kind_u8, flags = header[0], header[1]
                id_len, aux_len, payload_len = struct.unpack("<III", f.read(12))
                _iteration = struct.unpack("<q", f.read(8))[0]
                rec_id = f.read(id_len).decode("utf-8", errors="replace")
                aux = f.read(aux_len).decode("utf-8", errors="replace") if aux_len else ""
                payload = f.read(payload_len)

                # Kind 2 = PnfCandidate
                if kind_u8 == 2 and payload.startswith(b"PNF1"):
                    frag_u8 = payload[4]
                    shape_u8 = payload[5]
                    role_str = FRAGMENT_NAMES.get(frag_u8, "other")
                    dep_str = SHAPE_NAMES.get(shape_u8, "unresolved")

                    # Decode PNF1 body:
                    # [0..4]: b'PNF1'
                    # [4]: fragment as u8
                    # [5]: shape as u8
                    # [6..8]: 1, 0
                    # [8..16]: sentence_id as u64
                    # [16..20]: local_ordinal as u32
                    # [20..24]: head_ordinal as u32
                    # [24..28]: start_char as u32
                    # [28..32]: end_char as u32
                    # strings: orth, lemma, head_orth, head_lemma
                    pos = 32
                    try:
                        orth_len = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4
                        orth = payload[pos : pos + orth_len].decode("utf-8", errors="replace")
                        pos += orth_len

                        lemma_len = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4
                        lemma = payload[pos : pos + lemma_len].decode("utf-8", errors="replace")
                        pos += lemma_len

                        head_orth_len = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4
                        head_orth = payload[pos : pos + head_orth_len].decode("utf-8", errors="replace")
                        pos += head_orth_len

                        head_lemma_len = struct.unpack("<I", payload[pos : pos + 4])[0]
                        pos += 4
                        head_lemma = payload[pos : pos + head_lemma_len].decode("utf-8", errors="replace")
                        pos += head_lemma_len

                        sig = (role_str, lemma.lower(), dep_str)
                        global_multiset[sig] += 1

                        # aux contains document_ref e.g. "chat:<cid>"
                        cid = aux.replace("chat:", "") if aux else "unknown"
                        factors_by_thread[cid].append({
                            "candidate_id": rec_id,
                            "role": role_str,
                            "lemma": lemma,
                            "dependency": dep_str,
                            "head_lemma": head_lemma,
                        })
                    except Exception:
                        continue

        return dict(factors_by_thread), global_multiset
