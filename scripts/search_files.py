"""Read-only file locator: Everything ES first, bounded directory scan otherwise."""
import argparse
import csv
import ctypes
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
import uuid


def es_path(explicit):
    if explicit:
        candidate = Path(explicit)
        return str(candidate.resolve()) if candidate.is_file() else None
    candidates = [explicit, os.getenv("EVERYTHING_ES_PATH"),
                  str(Path(__file__).resolve().parents[1] / "bin" / "es.exe"),
                  shutil.which("es.exe")]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def selected_kind(args):
    # Advanced Everything syntax owns its filters unless --kind was explicit.
    return args.kind if args.kind else ("any" if args.query else "file")


def metadata(path):
    p = Path(path)
    try:
        info = p.stat()
        is_dir = p.is_dir()
        return {"path": str(p.absolute()), "name": p.name,
                "type": "directory" if is_dir else "file",
                "size_bytes": info.st_size if not is_dir else None,
                "modified": dt.datetime.fromtimestamp(info.st_mtime).astimezone().isoformat(),
                "exists": True, "status": "available"}
    except (FileNotFoundError, NotADirectoryError):
        return {"path": str(p.absolute()), "name": p.name, "exists": False,
                "status": "missing"}
    except OSError as exc:
        return {"path": str(p.absolute()), "name": p.name, "exists": None,
                "status": "inaccessible", "access_error": str(exc)}


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
    kind = selected_kind(args)
    if kind != "any":
        terms.append("file:" if kind == "file" else "folder:")
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
        records = [metadata(r["Filename"]) for r in rows[:args.limit]]
        inaccessible = sum(item.get("status") == "inaccessible" for item in records)
        warnings = ([str(inaccessible) + " matched paths could not be checked for access"]
                    if inaccessible else [])
        return {"backend": "everything", "scope": args.root or ["Everything indexed locations"],
                "query": query_for(args), "has_more": len(rows) > args.limit,
                "partial": inaccessible > 0, "results": records, "warnings": warnings}


def default_roots():
    paths = [Path.cwd()]
    if sys.platform == "win32":
        # Resolve shell Known Folders so OneDrive and manually redirected folders work.
        folders = {
            "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
            "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
            "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
        }
        for folder_id in folders.values():
            try:
                guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(folder_id).bytes_le)
                path_ptr = ctypes.c_void_p()
                shell32 = ctypes.WinDLL("shell32", use_last_error=True)
                shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
                shell32.SHGetKnownFolderPath.restype = ctypes.c_long
                result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                      ctypes.byref(path_ptr))
                if result == 0 and path_ptr.value:
                    try:
                        paths.append(Path(ctypes.wstring_at(path_ptr.value)))
                    finally:
                        ole32 = ctypes.WinDLL("ole32")
                        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
                        ole32.CoTaskMemFree.restype = None
                        ole32.CoTaskMemFree(path_ptr)
            except (AttributeError, OSError, ValueError):
                continue
    else:
        home = Path.home()
        paths.extend([home / "Desktop", home / "Documents", home / "Downloads"])
    roots = []
    seen = set()
    for path in paths:
        try:
            if path.is_dir():
                root = str(path.resolve())
                key = os.path.normcase(root)
                if key not in seen:
                    seen.add(key)
                    roots.append(root)
        except OSError:
            continue
    return roots


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
                        kind = selected_kind(args)
                        if kind == "file" and is_dir or kind == "directory" and not is_dir:
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
    selected = results[:args.limit]
    inaccessible = sum(item.get("status") == "inaccessible" for item in selected)
    if inaccessible:
        warnings.append(str(inaccessible) + " matched paths could not be checked for access")
    return {"backend": "scan", "scope": roots, "has_more": len(results) > args.limit,
            "partial": timed_out or skipped > 0 or inaccessible > 0,
            "results": selected, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="Literal filename substring (not wildcard or regex)")
    parser.add_argument("--query", help="Advanced Everything query; requires Everything")
    parser.add_argument("--root", action="append", help="Directory scope; repeat for several roots")
    parser.add_argument("--ext", action="append", default=[], help="Extension without dot; repeat")
    parser.add_argument("--kind", choices=["file", "directory", "any"],
                        help="Result type; name searches default to file, raw queries keep their own filters")
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
        diagnosis = {"es_path": executable, "platform": sys.platform,
                     "instance": args.instance or "default"}
        if executable:
            command = [executable]
            if args.instance:
                command += ["-instance", args.instance]
            try:
                version_result = subprocess.run(command + ["-get-everything-version"],
                                        capture_output=True, timeout=5)
                version_text = (version_result.stdout + version_result.stderr).decode(
                    "utf-8", errors="replace").strip()
                diagnosis["everything_version"] = version_text
            except (OSError, subprocess.TimeoutExpired) as exc:
                diagnosis["everything_version"] = None
                diagnosis["version_probe"] = str(exc)
            try:
                search_probe = [*command, "-argv", "-timeout", "5000", "-n", "1", "--",
                                "__everything_file_search_readiness_probe__"]
                probe_result = subprocess.run(search_probe, capture_output=True, timeout=8)
                probe_text = (probe_result.stdout + probe_result.stderr).decode(
                    "utf-8", errors="replace").strip()
                diagnosis.update({"search_available": probe_result.returncode == 0,
                                  "ipc_available": probe_result.returncode == 0,
                                  "search_probe": probe_text or "exit " + str(probe_result.returncode)})
                if probe_result.returncode == 8:
                    diagnosis["hint"] = (
                        "Search IPC is unavailable or the Everything database did not become ready "
                        "within the 5-second probe timeout")
            except (OSError, subprocess.TimeoutExpired) as exc:
                diagnosis.update({"search_available": False, "ipc_available": False,
                                  "search_probe": str(exc)})
        else:
            message = ("Specified --es-path does not exist or is not a file: " + args.es_path
                       if args.es_path else "es.exe not found")
            diagnosis.update({"everything_version": None, "search_available": False,
                              "ipc_available": False, "version_probe": message,
                              "search_probe": message})
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
                if args.es_path:
                    raise RuntimeError("Specified --es-path does not exist or is not a file: " + args.es_path)
                raise RuntimeError("es.exe not found")
            output = everything(args, executable)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            if args.backend == "everything" or args.query or args.instance:
                raise RuntimeError(str(exc) + "; advanced query/instance requires a working Everything search client") from exc
            warning = str(exc)
            if "ES exit 8:" in warning:
                warning += "; Error 8 can mean that the IPC window was unavailable or the database did not become ready before -timeout"
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

