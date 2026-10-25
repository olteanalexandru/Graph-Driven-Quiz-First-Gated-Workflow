#!/usr/bin/env python3
"""Measure how files talk to each other: imports that cross feature (domain) boundaries,
import cycles, hub files, string-keyed messaging and oversized files.

Domains come from .flow/config.json ("domains": {"checkout": "src/features/checkout"}).
Imports into "shared" folders (bus, helpers, ui) are allowed and not counted. Without
configured domains, the subfolders of a features/, modules/ or domains/ folder are used;
if there is none, cross-domain numbers are reported as n/a.

  python3 coupling.py              summary
  python3 coupling.py --md         markdown report (for docs/refactor/baseline.md)
  python3 coupling.py --json       numbers only, to track progress between scans

The refactor goal: cross-domain imports and cycles go to zero, because features talk
through the bus and shared helpers instead of importing each other.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flow_lib import DEFAULT_CONFIG, load_config, repo_root  # noqa: E402

SKIP_DIRS = {".git", "node_modules", ".next", "target", "dist", "build", "out", "coverage", ".turbo",
             "graphify-out", "playwright-report", "test-results", ".vercel", ".flow", "vendor"}
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
IMPORT_RX = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""")
RUST_USE_RX = re.compile(r"\buse\s+crate::(\w+)")
STRING_MSG_RX = re.compile(
    r"dispatchEvent\(|new\s+CustomEvent\(|\.emit\(\s*['\"]|\.on\(\s*['\"][\w:/.-]+['\"]|postMessage\(|BroadcastChannel\(")
LARGE_LINES = 400


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(TS_EXT + (".rs",)) and ".d.ts" not in name:
                yield Path(dirpath, name).relative_to(root).as_posix()


def resolve_ts(src, spec, aliases, files):
    if spec.startswith("."):
        base = os.path.normpath(os.path.join(os.path.dirname(src), spec)).replace(os.sep, "/")
    else:
        base = None
        for prefix, target in aliases.items():
            if spec.startswith(prefix):
                base = os.path.normpath(target.rstrip("/") + "/" + spec[len(prefix):]).replace(os.sep, "/")
                break
        if base is None:
            return None                        # package import
    for cand in [base] + [base + e for e in TS_EXT] + [f"{base}/index{e}" for e in TS_EXT]:
        if cand in files:
            return cand
    return base                                # unresolved, still useful for domain mapping


def default_domains(files):
    """Use the subfolders of a features/modules/domains folder when the repo has one."""
    for base in ("src/features", "src/modules", "src/domains", "features", "modules", "domains"):
        subs = sorted({f[len(base) + 1:].split("/")[0] for f in files
                       if f.startswith(base + "/") and f.count("/") > base.count("/") + 1})
        if subs:
            return {s: f"{base}/{s}" for s in subs}
    return {}


def domain_of(path, domains):
    best, best_len = None, -1
    for name, folder in domains.items():
        folder = folder.rstrip("/")
        if (path == folder or path.startswith(folder + "/")) and len(folder) > best_len:
            best, best_len = name, len(folder)
    return best


def tarjan(nodes, edges):
    """Strongly connected components, iterative (no recursion limit on big repos)."""
    index, low, on_stack, stack, comps, counter = {}, {}, set(), [], [], [0]
    for start in nodes:
        if start in index:
            continue
        work = [(start, iter(edges.get(start, ())))]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(edges.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    comps.append(sorted(comp))
    return comps


def scan(root, cfg):
    files = sorted(walk(root))
    fileset = set(files)
    domains = cfg.get("domains") or default_domains(files)
    shared = [s.rstrip("/") for s in cfg.get("shared") or []]
    aliases = cfg.get("aliases") or DEFAULT_CONFIG["aliases"]
    edges, cross, lines, messaging = defaultdict(set), [], {}, Counter()
    for f in files:
        try:
            text = (Path(root) / f).read_text("utf-8", "ignore")
        except OSError:
            continue
        lines[f] = text.count("\n") + 1
        messaging[f] = len(STRING_MSG_RX.findall(text))
        if f.endswith(".rs"):
            crate_root = f.split("src/")[0] + "src/" if "src/" in f else ""
            targets = set()
            for module in RUST_USE_RX.findall(text):
                base = crate_root + module
                targets.add(next((c for c in (base + ".rs", base + "/mod.rs") if c in fileset), base))
        else:
            targets = set()
            for m in IMPORT_RX.finditer(text):
                spec = next(g for g in m.groups() if g)
                t = resolve_ts(f, spec, aliases, fileset)
                if t:
                    targets.add(t)
        src_dom = domain_of(f, domains)
        for t in targets:
            if t != f:
                edges[f].add(t)
            dst_dom = domain_of(t, domains)
            if (src_dom and dst_dom and src_dom != dst_dom
                    and not any(t == s or t.startswith(s + "/") for s in shared)):
                cross.append((f, t, src_dom, dst_dom))
    real = {f: {t for t in ts if t in fileset} for f, ts in edges.items()}
    dom_edges = defaultdict(set)
    for _, _, a, b in cross:
        dom_edges[a].add(b)
    fan_in = Counter(t for ts in real.values() for t in ts)
    return {
        "files": len(files),
        "import_edges": sum(len(v) for v in real.values()),
        "domains": domains,
        "cross_domain_imports": len(cross),
        "cross_by_pair": Counter(f"{a} -> {b}" for _, _, a, b in cross).most_common(),
        "cross_by_file": Counter(f for f, _, _, _ in cross).most_common(),
        "domain_cycles": tarjan(sorted(domains), {k: sorted(v) for k, v in dom_edges.items()}),
        "file_cycles": tarjan(sorted(real), {k: sorted(v) for k, v in real.items()}),
        "hubs": fan_in.most_common(),
        "string_messaging": [(f, n) for f, n in messaging.most_common() if n],
        "large_files": sorted(((f, n) for f, n in lines.items() if n > LARGE_LINES), key=lambda x: -x[1]),
    }


def summary(r, top):
    cross = (str(r["cross_domain_imports"]) if r["domains"]
             else "n/a (set \"domains\" in .flow/config.json)")
    out = [f"{r['files']} files, {r['import_edges']} internal imports, {len(r['domains'])} domains",
           f"cross-domain imports: {cross}",
           f"domain cycles: {len(r['domain_cycles'])}, file import cycles: {len(r['file_cycles'])}",
           f"files with string-keyed messaging: {len(r['string_messaging'])}",
           f"files over {LARGE_LINES} lines: {len(r['large_files'])}"]
    if r["cross_by_pair"]:
        out.append("top cross-domain pairs: " + ", ".join(f"{p} ({n})" for p, n in r["cross_by_pair"][:top]))
    return "\n".join(out)


def markdown(r, top):
    md = ["# Coupling baseline", "", "```", summary(r, top), "```", ""]

    def table(title, head, rows):
        if not rows:
            return
        md.extend([f"## {title}", "", f"| {' | '.join(head)} |", "|" + "---|" * len(head)])
        md.extend(f"| {' | '.join(str(c) for c in row)} |" for row in rows[:top])
        md.append("")

    table("Domains", ["domain", "folder"], sorted(r["domains"].items()))
    table("Cross-domain imports by pair", ["from -> to", "imports"], r["cross_by_pair"])
    table("Files importing other domains", ["file", "cross-domain imports"], r["cross_by_file"])
    table("Most imported files", ["file", "importing files"], r["hubs"])
    table("Domain cycles", ["domains in the cycle"], [[" , ".join(c)] for c in r["domain_cycles"]])
    table("File import cycles", ["files in the cycle"], [[", ".join(c)] for c in r["file_cycles"]])
    table("String-keyed messaging (invisible to the graph)", ["file", "uses"], r["string_messaging"])
    table(f"Files over {LARGE_LINES} lines", ["file", "lines"], r["large_files"])
    return "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    root = repo_root(a.root or ".") or Path(a.root or ".").resolve()
    cfg = load_config(root) or dict(DEFAULT_CONFIG)
    r = scan(root, cfg)
    if a.json:
        keep = ("files", "import_edges", "cross_domain_imports")
        print(json.dumps({**{k: r[k] for k in keep}, "domain_cycles": len(r["domain_cycles"]),
                          "file_cycles": len(r["file_cycles"]), "string_messaging_files": len(r["string_messaging"]),
                          "large_files": len(r["large_files"])}, indent=2))
    elif a.md:
        print(markdown(r, a.top))
    else:
        print(summary(r, a.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
