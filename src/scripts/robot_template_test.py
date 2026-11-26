#!/usr/bin/env python3
"""
robot_template_test.py — Test ROBOT template expression patterns.

Dynamically constructs a minimal test TSV from the actual software.tsv columns
(one data row per column, one value per row) then runs each row through
`robot template` individually to report pass/fail.

Optionally merges the source ontology (+ imports) first so that label lookup
uses the full ontology — same as the real build — avoiding spurious failures
from missing labels or unknown prefixes.

Usage
-----
  # Merge from source ontology (recommended):
  python robot_template_test.py --ontology swo-edit.owl

  # Use pre-merged OWL (faster on re-runs):
  python robot_template_test.py --owl /tmp/swo-merged.owl

  # Schema-column tests + hand-crafted edge cases (FoodOn-style, failure cases):
  python robot_template_test.py --ontology swo-edit.owl --extra-cases

  # Show the generated test TSV without running ROBOT:
  python robot_template_test.py --ontology swo-edit.owl --dry-run

Options
-------
  --ontology OWL    OWL source file; imports resolved via catalog-v001.xml
                    (looked up beside the OWL file automatically).
  --owl OWL         Pre-merged OWL (skips the merge step).
  --catalog XML     Explicit ROBOT catalog path (overrides auto-detect).
  --schema YAML     schema.yaml [default: same directory as this script].
  --tsv TSV         software.tsv used to pick one real sample value per column
                    [default: ../software.tsv relative to this script].
  --out-dir DIR     Scratch directory for temp files [default: /tmp/robot_template_tests].
  --extra-cases     Also run hand-crafted edge-case tests (FoodOn-style, failure probes).
  --keep            Keep scratch files after the run (useful for inspecting failures).
  --dry-run         Print the generated test TSV and test plan; do not run ROBOT.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

# ---------------------------------------------------------------------------
# Paths relative to this script
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
DEFAULT_SCHEMA = SCRIPT_DIR / "schema.yaml"
DEFAULT_TSV = SCRIPT_DIR.parent / "software.tsv"
DEFAULT_OUT_DIR = Path("/tmp/robot_template_tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(text: str, maxlen: int = 48) -> str:
    """Safe filesystem slug from an arbitrary string."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)[:maxlen]


def _first_error_line(text: str) -> str:
    """Extract the most informative line from ROBOT stderr/stdout."""
    for line in text.splitlines():
        s = line.strip()
        if any(k in s for k in ("ERROR", "Exception", "PARSE", "Caused by:", "error:")):
            return s
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return "(no output)"


# ---------------------------------------------------------------------------
# Ontology merge
# ---------------------------------------------------------------------------

def merge_ontology(onto_path: Path, catalog: Path | None, out_path: Path) -> Path:
    """Run `robot merge` and return the output path."""
    cmd = ["robot"]
    if catalog and catalog.exists():
        cmd += ["--catalog", str(catalog)]
    cmd += ["merge", "--input", str(onto_path), "--output", str(out_path)]

    print(f"Merging {onto_path.name} → {out_path} …")
    r = subprocess.run(cmd, capture_output=True, text=True,
                       cwd=str(onto_path.parent))
    if r.returncode != 0:
        sys.exit(f"robot merge failed:\n{r.stderr}")
    print(f"  merged: {out_path.stat().st_size:,} bytes\n")
    return out_path


# ---------------------------------------------------------------------------
# Schema + TSV loading
# ---------------------------------------------------------------------------

def load_prefix_map(schema_path: Path) -> dict:
    with open(schema_path, encoding="utf-8") as f:
        return yaml.safe_load(f).get("prefixes", {})


_TEST_PREFIX_NAME = "T"
_TEST_PREFIX_IRI  = "http://test.swo.org/"


def _prefix_args(prefix_map: dict) -> list[str]:
    args = []
    for name, iri in prefix_map.items():
        args += ["--prefix", f"{name}: {iri}"]
    # Always add the test-IRI prefix so T:TSxxxx / T:EXxxxx resolve
    if _TEST_PREFIX_NAME not in prefix_map:
        args += ["--prefix", f"{_TEST_PREFIX_NAME}: {_TEST_PREFIX_IRI}"]
    return args


def read_tsv_columns(tsv_path: Path) -> tuple[list, list, list]:
    """
    Read software.tsv and return:
      headers     — row 0 (column names)
      robot_codes — row 1 (ROBOT template codes)
      data_rows   — rows 2+ as plain lists
    """
    with open(tsv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if len(rows) < 2:
        sys.exit(f"TSV {tsv_path} has fewer than 2 rows")
    return rows[0], rows[1], rows[2:]


def pick_sample_values(headers: list, robot_codes: list, data_rows: list) -> dict:
    """
    For each column, pick the first non-empty value from data rows.
    Returns dict: col_index -> {'header': ..., 'robot': ..., 'value': ...}
    """
    samples: dict[int, dict] = {}
    for i, (hdr, code) in enumerate(zip(headers, robot_codes)):
        if not code or code.upper() in ("ID", "TYPE", ""):
            continue
        for row in data_rows:
            val = row[i].strip() if i < len(row) else ""
            if val:
                samples[i] = {"header": hdr, "robot": code, "value": val}
                break
    return samples


# ---------------------------------------------------------------------------
# Test TSV construction
# ---------------------------------------------------------------------------

def build_test_plan(samples: dict) -> list[dict]:
    """
    Build a list of test-case dicts from the TSV sample values.

    Each entry in the list corresponds to one ROBOT template run:
      - col_idx, header, robot_code — the column being tested
      - rows       — list of [id, value] pairs to put in the TSV data section
      - case_name  — descriptive name for reporting

    For SPLIT columns we generate two cases: single value, then multi-value.
    """
    plan = []
    for col_idx, info in samples.items():
        hdr = info["header"]
        code = info["robot"]
        raw_val = info["value"]

        sep_match = re.search(r"SPLIT=(.)", code)
        sep = sep_match.group(1) if sep_match else None

        # Single-value test: strip to first segment if the raw value is multi
        if sep:
            first_val = raw_val.split(sep)[0].strip()
        else:
            first_val = raw_val.strip()

        plan.append({
            "col_idx": col_idx,
            "header": hdr,
            "robot": code,
            "row_id": f"T:TS{len(plan):04d}",
            "test_value": first_val,
            "case_name": f"{hdr}",
            "case_type": "single",
        })

        # Multi-value test for SPLIT columns
        if sep:
            parts = [p.strip() for p in raw_val.split(sep) if p.strip()]
            if len(parts) >= 2:
                multi_val = sep.join(parts[:2])
            else:
                multi_val = sep.join([parts[0], parts[0]])  # duplicate if only one found

            plan.append({
                "col_idx": col_idx,
                "header": hdr,
                "robot": code,
                "row_id": f"T:TS{len(plan):04d}",
                "test_value": multi_val,
                "case_name": f"{hdr} [split]",
                "case_type": "split",
            })

    return plan


# ---------------------------------------------------------------------------
# Extra hand-crafted test cases
# ---------------------------------------------------------------------------

# Known IRIs present in the merged SWO ontology (confirmed with merged OWL)
_SW_CURIE = "SWO:0000001"            # software — single-word label
_FMT_CURIE = "EDAM:format_2585"      # SBML format — in has_specified_data_input range
_FMT_CURIE2 = "EDAM:format_1915"     # Format (generic) — second value for split tests
_DATA_PROP = "SWO:0000086"           # has specified data input (IRI-based)
_DATA_PROP_LABEL = "has specified data input"  # label present in merged OWL


def extra_test_cases() -> list[dict]:
    """
    Hand-crafted cases probing edge-case % behaviours and FoodOn-style patterns.
    'expect' is 'pass', 'fail', or '' (unknown).
    """
    return [
        # ── Parent column variants ─────────────────────────────────────────
        {
            "header": "parent_label_singleword",
            "robot": "SC '%'",
            "row_id": "T:EX0000",
            "test_value": "software",
            "case_name": "SC '%' — single-word label (our current parent pattern)",
            "expect": "pass",
        },
        {
            "header": "parent_label_multiword",
            "robot": "SC '%'",
            "row_id": "T:EX0001",
            "test_value": "has specified data input",   # multi-word label
            "case_name": "SC '%' — multi-word label — EXPECTED FAIL",
            "expect": "fail",
        },
        {
            "header": "parent_curie_no_quotes",
            "robot": "SC %",           # no quotes around % — direct CURIE substitution
            "row_id": "T:EX0002",
            "test_value": _SW_CURIE,
            "case_name": "SC % — CURIE parent (no label lookup, FoodOn-style)",
            "expect": "pass",
        },
        # ── Restriction value variants ─────────────────────────────────────
        {
            "header": "restriction_curie_prop_curie_val",
            "robot": f"SC {_DATA_PROP} some %",
            "row_id": "T:EX0003",
            "test_value": _FMT_CURIE,
            "case_name": f"SC CURIE some % — restriction, CURIE value (our current style)",
            "expect": "pass",
        },
        {
            "header": "restriction_curie_prop_quoted_pct",
            "robot": f"SC {_DATA_PROP} some '%'",   # % wrapped in quotes
            "row_id": "T:EX0004",
            "test_value": _FMT_CURIE,
            "case_name": f"SC CURIE some '%' — restriction, QUOTED % — EXPECTED FAIL",
            "expect": "fail",
        },
        # ── FoodOn-style: quoted property label ───────────────────────────
        {
            "header": "restriction_quoted_prop_label",
            "robot": f"SC '{_DATA_PROP_LABEL}' some %",
            "row_id": "T:EX0005",
            "test_value": _FMT_CURIE,
            "case_name": f"SC 'property label' some % — FoodOn-style, CURIE value",
            "expect": "",   # unknown — this is what we're testing
        },
        {
            "header": "restriction_quoted_prop_label_split",
            "robot": f"SC '{_DATA_PROP_LABEL}' some % SPLIT=|",
            "row_id": "T:EX0006",
            "test_value": f"{_FMT_CURIE}|{_FMT_CURIE2}",
            "case_name": f"SC 'property label' some % SPLIT=| — FoodOn-style with multi-value",
            "expect": "",
        },
        {
            "header": "restriction_quoted_prop_label_quoted_pct",
            "robot": f"SC '{_DATA_PROP_LABEL}' some '%'",
            "row_id": "T:EX0007",
            "test_value": _FMT_CURIE,
            "case_name": f"SC 'property label' some '%' — FoodOn prop, QUOTED % — EXPECTED FAIL",
            "expect": "fail",
        },
    ]


# ---------------------------------------------------------------------------
# ROBOT runner
# ---------------------------------------------------------------------------

def run_robot_template(
    owl_path: Path,
    id_header: str,
    id_code: str,
    test_header: str,
    robot_code: str,
    row_id: str,
    test_value: str,
    out_dir: Path,
    slug: str,
    prefix_args: list[str],
) -> tuple[bool, str]:
    """
    Write a minimal 2-column TSV (ID + test column) with one data row,
    then run `robot template` on it.  Returns (success, error_text).
    """
    tsv_path = out_dir / f"{slug}.tsv"
    owl_out = out_dir / f"{slug}.owl"

    with open(tsv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow([id_header, test_header])
        w.writerow([id_code, robot_code])
        w.writerow([row_id, test_value])

    cmd = ["robot"] + prefix_args + [
        "template",
        "--input", str(owl_path),
        "--template", str(tsv_path),
        "--output", str(owl_out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr + r.stdout


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------

def run_plan(
    plan: list[dict],
    owl_path: Path,
    id_header: str,
    id_code: str,
    out_dir: Path,
    prefix_args: list[str],
    section_title: str,
) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"\n{'━' * 72}")
    print(section_title)
    print('━' * 72)

    for i, case in enumerate(plan):
        hdr = case["header"]
        code = case["robot"]
        val = case["test_value"]
        name = case.get("case_name", hdr)
        expect = case.get("expect", "")

        slug = _slug(f"{i:03d}_{name}")
        ok, stderr = run_robot_template(
            owl_path=owl_path,
            id_header=id_header,
            id_code=id_code,
            test_header=hdr,
            robot_code=code,
            row_id=case["row_id"],
            test_value=val,
            out_dir=out_dir,
            slug=slug,
            prefix_args=prefix_args,
        )

        status = "PASS" if ok else "FAIL"
        expect_note = ""
        if expect == "pass" and not ok:
            expect_note = "  ← UNEXPECTED FAIL"
        elif expect == "fail" and ok:
            expect_note = "  ← UNEXPECTED PASS"
        elif expect in ("pass", "fail"):
            expect_note = "  (expected)"

        print(f"  [{status}]{expect_note}")
        print(f"         {name}")
        print(f"         robot: {code!r}")
        print(f"         value: {val!r}")
        if not ok:
            err = _first_error_line(stderr)
            print(f"         error: {err}")

        results.append({
            "case_name": name,
            "header": hdr,
            "robot": code,
            "value": val,
            "pass": ok,
            "expect": expect,
            "error": _first_error_line(stderr) if not ok else "",
        })

    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(all_results: list[dict]) -> int:
    """Print final summary table. Returns exit code (0=all pass, 1=some fail)."""
    total = len(all_results)
    passed = sum(1 for r in all_results if r["pass"])
    failed = total - passed
    unexpected = sum(
        1 for r in all_results
        if (not r["pass"] and r.get("expect") == "pass")
        or (r["pass"] and r.get("expect") == "fail")
    )

    print(f"\n{'━' * 72}")
    print(f"Summary: {passed}/{total} passed, {failed} failed"
          + (f", {unexpected} unexpected" if unexpected else ""))
    print('━' * 72)

    if failed:
        print("\nFailed tests:")
        for r in all_results:
            if not r["pass"]:
                marker = "✗"
                if r.get("expect") == "fail":
                    marker = "✓ (expected)"
                print(f"  {marker}  {r['case_name']!r}")
                print(f"      robot: {r['robot']!r}")
                if r["error"]:
                    # Wrap long error lines
                    err_lines = textwrap.wrap(r["error"], width=66, initial_indent="      ", subsequent_indent="      ")
                    print("\n".join(err_lines))

    if unexpected:
        print("\nUnexpected results (investigate these):")
        for r in all_results:
            u = (not r["pass"] and r.get("expect") == "pass") or \
                (r["pass"] and r.get("expect") == "fail")
            if u:
                got = "PASS" if r["pass"] else "FAIL"
                exp = r["expect"].upper()
                print(f"  expected {exp} but got {got}: {r['case_name']!r}")

    # Treat expected failures as acceptable; only exit non-zero for unexpected outcomes
    if unexpected > 0:
        return 1
    all_accounted = all(r["pass"] or r.get("expect") == "fail" for r in all_results)
    return 0 if all_accounted else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    owl_grp = p.add_mutually_exclusive_group(required=True)
    owl_grp.add_argument("--ontology", metavar="OWL",
                         help="OWL source file; imports merged via catalog.")
    owl_grp.add_argument("--owl", metavar="OWL",
                         help="Pre-merged OWL (skips merge step).")
    p.add_argument("--catalog", metavar="XML", default=None,
                   help="Explicit ROBOT catalog path.")
    p.add_argument("--schema", metavar="YAML", default=str(DEFAULT_SCHEMA),
                   help=f"schema.yaml [{DEFAULT_SCHEMA}]")
    p.add_argument("--tsv", metavar="TSV", default=str(DEFAULT_TSV),
                   help=f"software.tsv [{DEFAULT_TSV}]")
    p.add_argument("--out-dir", metavar="DIR", default=str(DEFAULT_OUT_DIR),
                   help=f"Scratch directory [{DEFAULT_OUT_DIR}]")
    p.add_argument("--extra-cases", action="store_true",
                   help="Also run hand-crafted edge-case tests.")
    p.add_argument("--keep", action="store_true",
                   help="Keep scratch files after run.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print test plan and generated TSVs; do not run ROBOT.")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Merge ontology ───────────────────────────────────────────────────
    if args.ontology:
        onto_path = Path(args.ontology)
        if not onto_path.is_absolute():
            onto_path = Path.cwd() / onto_path
        if args.catalog:
            catalog = Path(args.catalog)
        else:
            catalog = onto_path.parent / "catalog-v001.xml"
        owl_path = merge_ontology(onto_path, catalog, out_dir / "merged.owl")
    else:
        owl_path = Path(args.owl)
        if not owl_path.exists():
            sys.exit(f"OWL file not found: {owl_path}")

    # ── Load schema prefixes ──────────────────────────────────────────────
    prefix_map = load_prefix_map(Path(args.schema))
    pargs = _prefix_args(prefix_map)

    # ── Load TSV columns + sample values ─────────────────────────────────
    headers, robot_codes, data_rows = read_tsv_columns(Path(args.tsv))
    id_header = headers[0]      # "Ontology ID"
    id_code = robot_codes[0]    # "ID"

    samples = pick_sample_values(headers, robot_codes, data_rows)
    plan = build_test_plan(samples)

    print(f"TSV columns:   {len(headers)}")
    print(f"Test columns:  {len(samples)}")
    print(f"Test cases:    {len(plan)}"
          + (" (schema)" if not args.extra_cases else f" (schema) + {len(extra_test_cases())} (extra)"))

    if args.dry_run:
        print(f"\n{'━' * 72}")
        print("Test plan (dry-run — ROBOT not invoked)")
        print('━' * 72)
        for case in plan:
            print(f"  {case['case_name']!r}")
            print(f"    robot: {case['robot']!r}")
            print(f"    value: {case['test_value']!r}")
        if args.extra_cases:
            print(f"\n{'━' * 72}")
            print("Extra cases")
            print('━' * 72)
            for case in extra_test_cases():
                exp = f" [expect={case['expect']}]" if case["expect"] else ""
                print(f"  {case['case_name']!r}{exp}")
                print(f"    robot: {case['robot']!r}")
                print(f"    value: {case['test_value']!r}")
        return 0

    # ── Run schema-column tests ───────────────────────────────────────────
    all_results = run_plan(
        plan=plan,
        owl_path=owl_path,
        id_header=id_header,
        id_code=id_code,
        out_dir=out_dir / "schema_tests",
        prefix_args=pargs,
        section_title="Schema.yaml column tests (one sample value per column)",
    )

    # ── Run extra edge-case tests ─────────────────────────────────────────
    if args.extra_cases:
        extra = extra_test_cases()
        extra_results = run_plan(
            plan=extra,
            owl_path=owl_path,
            id_header=id_header,
            id_code=id_code,
            out_dir=out_dir / "extra_tests",
            prefix_args=pargs,
            section_title="Edge-case / FoodOn-style tests",
        )
        all_results.extend(extra_results)

    # ── Summary ───────────────────────────────────────────────────────────
    exit_code = print_summary(all_results)

    if not args.keep:
        # Remove output OWL files; keep TSVs for inspection
        for f in out_dir.rglob("*.owl"):
            if f.name != "merged.owl":
                try:
                    f.unlink()
                except OSError:
                    pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
