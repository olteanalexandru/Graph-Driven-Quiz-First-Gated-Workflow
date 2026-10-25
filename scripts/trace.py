#!/usr/bin/env python3
"""Keep one item's spec, design, baseline, performance budgets, audit and tests in sync.

  python3 trace.py check ITEM-042 [--stage plan|build|review] [--drift] [--json]

plan    citations: design files and baseline rows cite decisions (D#) that exist, every AC
        cites a decision, assumption (A#) or finding (U#) that exists, change and remove
        rows in baseline.md cite a decision, a deep-clarified spec has no open questions.
build   also tests: every AC, every kept feature (F#) without an existing test, and every
        performance budget (PB#) has a test tagged @item:<ID>; no test names an unknown ID.
review  also the audit: no critical or major finding (severity 3-4) still open.
--drift (on for build and review) sequence-diagram participants and messages.md message
        types that cannot be found in the code. Warnings only: confirm with the graph.

Exit 1 when there are errors. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_lib as lib  # noqa: E402

AC_LINE = re.compile(r"(?m)^\s*[-*]\s*(AC\d+)\b[ \t]*(?:\(([^)\n]*)\))?")
TEST_TITLE = re.compile(r"""["'`]\s*(AC|F|PB|U)(\d+)\b""")
RUST_TEST = re.compile(r"\bfn\s+(ac|f|pb|u)(\d+)_", re.I)
PARTICIPANT = re.compile(r"^\s*(?:create\s+)?(participant|actor)\s+(.+?)\s*$")
MESSAGE_TYPE = re.compile(r"`([a-z][\w-]*/[A-Za-z][\w-]*)`")
CODE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read(path):
    return path.read_text("utf-8", "ignore") if path.is_file() else ""


def table_ids(text, prefix):
    return {lib.first_col(r): r for t in lib.md_tables(text) for r in t
            if re.fullmatch(prefix + r"\d+", lib.first_col(r))}


def severity(cell):
    m = re.search(r"[0-4]", cell or "")
    return int(m.group(0)) if m else 0


def collect_tests(root, item):
    """Test IDs (AC1, F2, PB1, U3) -> files, from titles tagged @item:<ID> and Rust tests in files named with the item."""
    found = {}
    out = lib.git(root, "grep", "-n", "-I", "--untracked", "-F", f"@item:{item}", "--", ".", ":(exclude)docs")
    for line in (out or "").splitlines():
        path, _, rest = line.partition(":")
        m = TEST_TITLE.search(rest.partition(":")[2])
        if m:
            found.setdefault(m.group(1) + m.group(2), set()).add(path)
    key = item.lower().replace("-", "_")
    for p in Path(root).glob("tests/**/*.rs"):
        if key in p.name.lower().replace("-", "_"):
            for m in RUST_TEST.finditer(read(p)):
                found.setdefault(m.group(1).upper() + m.group(2), set()).add(p.relative_to(root).as_posix())
    return found


def in_code(root, tokens):
    if not tokens:
        return True
    args = ["git", "grep", "-q", "-w", "-I", "--untracked", "-F"]
    for t in tokens:
        args += ["-e", t]
    args += ["--", ".", ":(exclude)docs"]
    try:
        return subprocess.run(args, cwd=str(root), capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True                       # cannot tell: stay quiet


def participants(text):
    """(id, label) of non-actor participants in a sequence diagram, minus those listed in '%% external: A, B'."""
    if not re.search(r"(?m)^\s*sequenceDiagram\b", text):
        return []
    external = set()
    for m in re.finditer(r"(?m)^\s*%%\s*external:\s*(.+)$", text):
        external |= {x.strip() for x in m.group(1).split(",")}
    out = []
    for line in text.splitlines():
        m = PARTICIPANT.match(line)
        if not m or m.group(1) == "actor":
            continue
        decl = re.sub(r"@\{.*?\}", "", m.group(2)).strip()
        ident, _, label = decl.partition(" as ")
        ident, label = ident.strip(), label.strip() or ident.strip()
        if ident not in external and label not in external:
            out.append((ident, label))
    return out


def code_tokens(label):
    tokens = [t for t in CODE_TOKEN.findall(label) if len(t) >= 3]
    codeish = [t for t in tokens if "_" in t or re.search(r"[a-z][A-Z]", t)]
    return codeish or tokens


def check(root, cfg, item, stage, drift):
    errors, warnings = [], []
    d = lib.item_dir(root, item)
    spec_text = read(d / "spec.md")
    if not spec_text:
        return [f"docs/items/{item}/spec.md not found"], []
    spec = lib.parse_frontmatter(spec_text)
    decisions = set(re.findall(r"(?m)^\|\s*(D\d+)\s*\|", spec_text))
    assumptions = set(re.findall(r"(?m)^\|\s*(A\d+)\s*\|", spec_text))
    acs = {m.group(1): re.findall(r"\b([DAU]\d+)\b", m.group(2) or "") for m in AC_LINE.finditer(spec_text)}
    findings = table_ids(read(d / "audit.md"), "U")
    known = decisions | assumptions | set(findings)

    # Spec: acceptance criteria and open questions.
    if not acs:
        errors.append("spec.md has no acceptance criteria (- AC1 (D1): Given ..., when ..., then ...)")
    for ac, cited in sorted(acs.items()):
        if not cited:
            errors.append(f"{ac} cites no decision, assumption or finding")
        for c in cited:
            if c not in known:
                errors.append(f"{ac} cites {c}, which is not in spec.md" + (" or audit.md" if c.startswith("U") else ""))
    open_q = [ln.strip()[1:].strip() for ln in lib.md_section(spec_text, "Open questions").splitlines()
              if ln.strip().startswith(("-", "*"))]
    open_q = [q for q in open_q if q and q.lower().rstrip(".") not in ("none", "-")]
    if open_q:
        msg = f"{len(open_q)} open question(s) in spec.md: {open_q[0][:80]}"
        (errors if spec.get("clarify") == "deep" else warnings).append(msg)

    # Design files and baseline cite decisions that exist.
    cited_anywhere = {c for v in acs.values() for c in v}
    for sub in lib.design_files(root, item):
        for c in sorted(set(re.findall(r"\b(D\d+)\b", read(d / sub)))):
            cited_anywhere.add(c)
            if c not in decisions:
                errors.append(f"{sub} cites {c}, which is not in spec.md")
    rows = lib.baseline_rows(root, item)
    for row in rows:
        fid, plan = lib.first_col(row), row.get("plan", "").strip().lower()
        if plan.startswith(("change", "remove")):
            cites = re.findall(r"\b(D\d+)\b", row.get("plan", ""))
            if not cites or any(c not in decisions for c in cites):
                errors.append(f"baseline {fid} is planned as '{row.get('plan')}' without a decision in spec.md; "
                              f"changing or removing a feature needs the user's approval as a D# row")
        elif plan and not plan.startswith("keep"):
            errors.append(f"baseline {fid}: plan must be keep, change (D#) or remove (D#), not '{row.get('plan')}'")
    for c in sorted(decisions - cited_anywhere):
        warnings.append(f"{c} is not cited by any AC, design file or baseline row")

    budgets = table_ids(read(d / "performance.md"), "PB")

    if stage in ("build", "review"):
        tests = collect_tests(root, item)
        for ac in sorted(acs):
            if ac not in tests:
                errors.append(f"{ac} has no test titled '{ac} ... @item:{item}'")
        for row in rows:
            fid = lib.first_col(row)
            if not row.get("plan", "keep").strip().lower().startswith("keep"):
                continue
            paths = lib.test_paths(row.get("tests", ""))
            missing = [p for p in paths if not (Path(root) / p).is_file()]
            for p in missing:
                errors.append(f"baseline {fid} names {p}, which does not exist")
            if not (set(paths) - set(missing)) and fid not in tests:
                errors.append(f"baseline {fid} (keep) has no test: add a characterization spec '{fid} ... @baseline @item:{item}'")
        for pb in sorted(budgets):
            if pb not in tests:
                errors.append(f"performance budget {pb} has no test titled '{pb} ... @perf @item:{item}'")
        valid = set(acs) | {lib.first_col(r) for r in rows} | set(budgets) | set(findings)
        for tid in sorted(tests):
            if tid not in valid:
                errors.append(f"test {tid} in {', '.join(sorted(tests[tid]))} matches nothing in the item's docs")

    if stage == "review":
        for uid, row in sorted(findings.items()):
            status = row.get("status", "open").strip().lower()
            if severity(row.get("severity")) >= 3 and status.startswith("open"):
                errors.append(f"audit finding {uid} (severity {severity(row.get('severity'))}) is still open; "
                              f"fix it or record the user's acceptance as a D# trade-off")

    if drift or stage in ("build", "review"):
        for sub in lib.design_files(root, item):
            if not sub.endswith(".mmd"):
                continue
            for ident, label in participants(read(d / sub)):
                if not in_code(root, code_tokens(label) or [ident]):
                    warnings.append(f"{sub}: participant '{label}' not found in the code (new, renamed or drifted?)")
        for msg in sorted(set(MESSAGE_TYPE.findall(read(d / "messages.md")))):
            if lib.git(root, "grep", "-q", "-I", "--untracked", "-F", msg, "--", ".", ":(exclude)docs") is None:
                warnings.append(f"messages.md: {msg} not found in the code")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("item")
    p.add_argument("--stage", choices=["plan", "build", "review"], default="plan")
    p.add_argument("--drift", action="store_true")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = lib.repo_root(args.root or ".")
    if root is None:
        print("not inside a git repository")
        return 2
    cfg = lib.load_config(root) or dict(lib.DEFAULT_CONFIG)
    errors, warnings = check(root, cfg, args.item, args.stage, args.drift)
    if args.json:
        print(json.dumps({"item": args.item, "stage": args.stage, "errors": errors, "warnings": warnings}, indent=2))
    else:
        print(f"trace {args.item} ({args.stage}): {len(errors)} errors, {len(warnings)} warnings")
        for e in errors:
            print(f"ERROR {e}")
        for w in warnings:
            print(f"WARN  {w}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
