"""Local rule candidates: evidence is retained and no automatic confirmation occurs."""
from __future__ import annotations

import copy
import hashlib
import re

_COMMANDS = set("frac dfrac tfrac sqrt partial sum prod int iint iiint lim infty alpha beta gamma delta epsilon theta lambda mu pi sigma phi omega Gamma Delta Theta Lambda Sigma Phi Omega sin cos tan log ln exp min max sup inf left right cdot times div pm mp le leq ge geq ne neq approx equiv to mapsto in notin subset subseteq supset supseteq cup cap forall exists neg land lor lnot implies iff quad qquad text mathrm mathbf mathit mathcal mathbb operatorname overline underline vec hat bar dot ddot ldots cdots vdots ddots begin end matrix pmatrix bmatrix vmatrix Vmatrix cases aligned align gather split hline rowcolor displaystyle textstyle binom choose angle perp parallel vert lvert rvert lVert rVert mid dots ell Re Im det mod pmod bmod top bot emptyset setminus backslash braces lbrace rbrace lbrack rbrack percent degree prime colon underbrace overbrace overset underset substack limits nolimits label tag not nonumber today".split())
# Deliberately no require/autoload, macro definition, URL, HTML, file or style primitives.
_COMMANDS -= {"rowcolor", "label", "today"}
_ENVIRONMENTS = {"matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "cases", "aligned", "gathered", "split"}
_TERMS = [("partial derivative", "偏导数"), ("derivative", "导数"), ("integral", "积分"),
          ("eigenvalue", "特征值"), ("vector space", "向量空间"), ("polynomial", "多项式")]


def validate_latex(latex: str) -> None:
    """Validate our bounded MathJax subset, raising ValueError for unsafe input.

    This is a safety check; rendering and mathematical correctness are separate checks.
    """
    if not isinstance(latex, str) or not latex.strip() or len(latex) > 4096:
        raise ValueError("FORMULA_UNSAFE: formula must contain 1-4096 characters")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in latex):
        raise ValueError("FORMULA_UNSAFE: control characters")
    if re.search(r"(?:https?|file|data|javascript):|[<>]|%", latex, re.I):
        raise ValueError("FORMULA_UNSAFE: external content, markup and comments are forbidden")
    commands = re.findall(r"\\([A-Za-z]+)", latex)
    unknown = set(commands) - _COMMANDS
    if unknown:
        raise ValueError("FORMULA_UNSAFE: unsupported command " + sorted(unknown)[0])
    # Every slash must introduce a command or an allowed mathematical delimiter.
    if re.search(r"\\(?![A-Za-z]|[\\{},;:! _|])", latex.replace("\\\\", "")):
        raise ValueError("FORMULA_UNSAFE: invalid escape")
    depth = 0
    for char in re.sub(r"\\[{}]", "", latex):
        if char == "{":
            depth += 1
            if depth > 32:
                raise ValueError("FORMULA_UNSAFE: nesting limit")
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("FORMULA_UNSAFE: unbalanced braces")
    if depth:
        raise ValueError("FORMULA_UNSAFE: unbalanced braces")
    stack = []
    for op, env in re.findall(r"\\(begin|end)\{([^{}]+)\}", latex):
        if env not in _ENVIRONMENTS:
            raise ValueError("FORMULA_UNSAFE: unsupported environment")
        if op == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            raise ValueError("FORMULA_UNSAFE: unmatched environment")
    if stack:
        raise ValueError("FORMULA_UNSAFE: unmatched environment")


def _candidate(text: str) -> tuple[str, str | None, list[str]] | None:
    patterns = [
        (r"x squared plus (?:two|2) x plus (?:one|1)", "x^2 + 2x + 1", []),
        (r"x\s*的?平方\s*加\s*(?:二|2)\s*x\s*加\s*(?:一|1)", "x^2 + 2x + 1", []),
        (r"e\s*的\s*x\s*次方", "e^x", []),
        (r"e (?:to the (?:power of )?)?x", "e^x", []),
        (r"one over x plus one", None, ["AMBIGUOUS_DENOMINATOR", "候选: \\frac{1}{x}+1 | \\frac{1}{x+1}"]),
        (r"(?:一|1)\s*除以\s*x\s*加\s*(?:一|1)", None, ["AMBIGUOUS_DENOMINATOR", "候选: \\frac{1}{x}+1 | \\frac{1}{x+1}"]),
    ]
    for pattern, latex, warnings in patterns:
        found = re.search(pattern, text, re.I)
        if found:
            return found.group(0), latex, ["RULE_CANDIDATE_REQUIRES_REVIEW", *warnings]
    found = re.search(r"\b([a-z]) (squared|cubed)\b", text, re.I)
    if found:
        return found.group(0), f"{found[1]}^{2 if found[2].lower() == 'squared' else 3}", ["RULE_CANDIDATE_REQUIRES_REVIEW"]
    return None


def analyze(project: dict) -> dict:
    """Return complete entity lists with conservative speech candidates added.

    Source transcripts and existing translations are never rewritten. Rules only
    link candidates to segments, so a translator can introduce exact placeholders.
    """
    result = {name: copy.deepcopy(project.get(name, [])) for name in ("segments", "formulas", "glossary", "notes")}
    known = {f["id"] for f in result["formulas"]}
    existing = {(f.get("raw"), e.get("start_ms"), e.get("end_ms")): f["id"]
                for f in result["formulas"] for e in f.get("evidence", []) if e.get("kind") == "speech"}
    existing_terms = {(term["source"].casefold(), term["target"].casefold()): term["id"] for term in result["glossary"]}
    source_is_english = project.get("languages", {}).get("source", "en").startswith("en")
    for segment in result["segments"]:
        if segment.get("review") == "confirmed":
            continue
        source_text = segment.get("source_text", "")
        occupied = []
        for english, chinese in _TERMS:
            source, target = (english, chinese) if source_is_english else (chinese, english)
            pattern = r"\b" + re.escape(source) + r"\b" if source_is_english else re.escape(source)
            found = re.search(pattern, source_text, re.I)
            if not found or any(a <= found.start() < b for a, b in occupied):
                continue
            occupied.append(found.span())
            key = (source.casefold(), target.casefold())
            term_id = existing_terms.get(key)
            if term_id is None:
                digest = hashlib.sha256((segment["id"] + "\0term\0" + source).encode()).hexdigest()[:16]
                term_id = "term-" + digest
                result["glossary"].append({"id": term_id, "source": source, "target": target,
                    "context": "本地候选，需结合课程语境确认。原句：" + source_text,
                    "locked": False, "evidence": [{"id": "ev-term-" + digest, "kind": "speech",
                        "start_ms": segment["start_ms"], "end_ms": segment["end_ms"]}]})
                existing_terms[key] = term_id
            if term_id not in segment["term_ids"]:
                segment["term_ids"].append(term_id)
        candidate = _candidate(segment.get("source_text", ""))
        if candidate is None:
            continue
        raw, latex, warnings = candidate
        key = (raw, segment["start_ms"], segment["end_ms"])
        formula_id = existing.get(key)
        if formula_id is None:
            digest = hashlib.sha256((segment["id"] + "\0" + raw).encode()).hexdigest()[:16]
            formula_id = "formula-" + digest
            if formula_id in known:
                continue
            result["formulas"].append({"id": formula_id, "raw": raw, "latex": latex,
                "spoken_source": raw, "spoken_target": "", "review": "needs_review",
                "warnings": warnings, "evidence": [{"id": "ev-" + digest,
                    "kind": "speech", "start_ms": segment["start_ms"], "end_ms": segment["end_ms"]}]})
            known.add(formula_id)
        if formula_id not in segment["formula_ids"]:
            segment["formula_ids"].append(formula_id)
            # Adding a mathematical reference invalidates prior acceptance.
            segment["review"] = "needs_review"
    result["usage"] = {"provider": "local-rules", "calls": 0, "cost_cny": 0.0}
    return result
