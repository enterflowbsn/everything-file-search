"""Read-only file locator: Everything ES first, bounded directory scan otherwise."""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def es_path(explicit):
    candidates = [explicit, os.getenv("EVERYTHING_ES_PATH"),
                  str(Path(__file__).resolve().parents[1] / "bin" / "es.exe"),
                  shutil.which("es.exe")]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def metadata(path):
    p = Path(path)
    try:
        stat = p.stat()
        return {"path": str(p.absolute()), "name": p.name,
                "type": "directory" if p.is_dir() else "file",
                "size_bytes": stat.st_size if p.is_file() else None,
                "modified": dt.datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
                "exists": True}
    except OSError:
        return {"path": str(p.absolute()), "name": p.name, "exists": False}


def date(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD") from exc


def query_for(args):
    terms = [args.query] if args.query else []
    if args.name:
        # Quote the PCRE pattern as a query term; hex-escape literal quotes.
        pattern = re.escape(args.name).replace('"', r'\x22')
        terms.append('regex:"' + pattern + '"')
    if args.kind != "any":
        terms.append("file:" if args.kind == "file" else "folder:")
    if args.ext:
        terms.append("ext:" + ";".join(args.ext))
    if args.after:
        terms.append("dm:>=" + args.after.isoformat())
    if args.before:
        terms.append("dm:<" + args.before.isoformat())
    if args.root:
        # -path accepts a single root; a query OR group supports several.
        paths = [str(Path(p).resolve()).rstrip("\\/") + os.sep for p in args.root]
        if any('"' in p for p in paths):
            raise ValueError("Root paths cannot contain double quotes")
        terms.append("<" + "|".join('path:"' + p + '"' for p in paths) + ">")
    return " ".join(terms)


def everything(args, executable):
    with tempfile.TemporaryDirectory(prefix="everything-search-") as temp:
        output = Path(temp) / "results.efu"
        command = [executable, "-argv", "-timeout", str(int(args.timeout * 1000)),
                   "-n", str(args.limit + 1), "-sort", "date-modified-descending",
                   "-export-efu", str(output)]
        if args.instance:
            command += ["-instance", args.instance]
        # -search reparses embedded quotes; -- preserves the query with -argv.
        command += ["--", query_for(args)]
        result = subprocess.run(command, capture_output=True, timeout=args.timeout + 3)
        if result.returncode:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise RuntimeError("ES exit " + str(result.returncode) + ": " + message)
        if not output.exists():
            raise RuntimeError("ES did not produce an export file")
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if rows and "Filename" not in rows[0]:
            raise RuntimeError("Unexpected EFU columns")
        return {"backend": "everything", "scope": args.root or ["Everything indexed locations"],
                "query": query_for(args), "has_more": len(rows) > args.limit,
                "partial": False, "results": [metadata(r["Filename"]) for r in rows[:args.limit]],
                "warnings": []}


def default_roots():
    home = Path.home()
    return [str(p) for p in [Path.cwd(), home / "Desktop", home / "Documents", home / "Downloads"]
            if p.is_dir()]


def scan(args):
    roots = list(dict.fromkeys(str(Path(p).resolve()) for p in (args.root or default_roots())))
    stack = []
    warnings = []
    for root in roots:
        if not Path(root).is_dir():
            raise ValueError("Search root is not an accessible directory: " + root)
        stack.append(root)
    deadline = time.monotonic() + args.timeout
    results = []
    seen = set()
    skipped = 0
    timed_out = False
    while stack and len(results) <= args.limit:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        directory = stack.pop()
        key = os.path.normcase(directory)
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    try:
                        is_dir = entry.is_dir()
                        # Junctions/symlinks are not traversed to avoid cycles and scope escapes.
                        reparse = bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                                       & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                        if is_dir and not entry.is_symlink() and not reparse:
                            stack.append(entry.path)
                        if args.kind == "file" and is_dir or args.kind == "directory" and not is_dir:
                            continue
                        if args.name and args.name.casefold() not in entry.name.casefold():
                            continue
                        if args.ext and (is_dir or Path(entry.name).suffix.lstrip(".").casefold() not in args.ext):
                            continue
                        modified = dt.datetime.fromtimestamp(entry.stat().st_mtime).date()
                        if args.after and modified < args.after or args.before and modified >= args.before:
                            continue
                        results.append(metadata(entry.path))
                        if len(results) > args.limit:
                            break
                    except OSError:
                        skipped += 1
        except OSError:
            skipped += 1
        if timed_out:
            break
    if timed_out:
        warnings.append("Time limit reached; search is incomplete")
    if skipped:
        warnings.append(str(skipped) + " inaccessible entries/directories skipped")
    return {"backend": "scan", "scope": roots, "has_more": len(results) > args.limit,
            "partial": timed_out or skipped > 0,
            "results": results[:args.limit], "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Literal filename substring (not wildcard or regex)")
    parser.add_argument("--query", help="Advanced Everything query; requires Everything")
    parser.add_argument("--root", action="append", help="Directory scope; repeat for several roots")
    parser.add_argument("--ext", action="append", default=[], help="Extension without dot; repeat")
    parser.add_argument("--kind", choices=["file", "directory", "any"], default="file")
    parser.add_argument("--after", type=date, help="Modified on/after YYYY-MM-DD")
    parser.add_argument("--before", type=date, help="Modified before YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--backend", choices=["auto", "everything", "scan"], default="auto")
    parser.add_argument("--es-path")
    parser.add_argument("--instance", help="Everything named instance")
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 1000 or not 0 < args.timeout <= 120:
        parser.error("limit must be 1..1000; timeout must be greater than 0 and at most 120")
    args.ext = [ext.lstrip(".").casefold() for ext in args.ext]
    if any(not re.fullmatch(r"[\w-]+", ext) for ext in args.ext):
        parser.error("Invalid extension")
    if args.after and args.before and args.after >= args.before:
        parser.error("--after must be earlier than --before")
    if args.root:
        for root in args.root:
            if not Path(root).is_dir():
                parser.error("Search root is not an accessible directory: " + root)
    executable = es_path(args.es_path)
    if args.diagnose:
        diagnosis = {"es_path": executable, "platform": sys.platform}
        if executable:
            try:
                result = subprocess.run([executable, "-get-everything-version"],
                                        capture_output=True, timeout=5)
                diagnosis.update({"ipc_available": result.returncode == 0,
                                  "probe": (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()})
            except (OSError, subprocess.TimeoutExpired) as exc:
                diagnosis.update({"ipc_available": False, "probe": str(exc)})
        print(json.dumps(diagnosis, ensure_ascii=False, indent=2))
        return
    if not any([args.name, args.query, args.ext, args.after, args.before]):
        parser.error("Specify a name, query, extension, or date filter")
    if args.query and args.backend == "scan":
        parser.error("--query cannot be interpreted by the scan backend")
    warning = None
    if args.backend != "scan":
        try:
            if not executable:
                raise RuntimeError("es.exe not found")
            output = everything(args, executable)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            if args.backend == "everything" or args.query or args.instance:
                raise RuntimeError(str(exc) + "; advanced query/instance requires accessible Everything IPC") from exc
            warning = str(exc)
            output = scan(args)
    else:
        output = scan(args)
    if warning:
        output["warnings"].insert(0, "Everything unavailable; searched directories instead: " + warning)
    output["returned"] = len(output["results"])
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
