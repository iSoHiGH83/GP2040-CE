#!/usr/bin/env python3
"""
GP2040-CE All-in-One Patch & Multi-Board Build Script (10 Makro-Slots + Web-Build + ALLE Boards)
================================================================================================
Repository: https://github.com/iSoHiGH83/GP2040-CE.git

Dieses All-in-One-Skript automatisiert alle Schritte auf Windows, Linux und macOS:
  1. Klont das Repository inkl. Submodules (oder nutzt einen bestehenden Ordner)
  2. Erhoeht das Makro-Limit in Protobuf, C++-Firmware und Web-Configurator (www/) auf 10
     (optional auch Reduzierung von Macro.macroInputs fuer RP2040-Flash-Limits)
  3. Fuehrt den Web-Build einmalig aus (npm install && npm run build)
  4. Erkennt automatisch ALLE Boards aus configs/*/BoardConfig.h
  5. Kompiliert fuer jedes Board isoliert die Firmware (.uf2), sammelt alle fertigen
     Dateien zentral in dist/ und erstellt einen Build-Bericht (dist/build_report.txt)

Beispiele:
  # Standard: Klont, patcht auf 10 Makros, baut Web-UI + ALLE Boards in configs/
  python gp2040_10macros_aio.py

  # Alle verfuegbaren Boards anzeigen (ohne zu bauen)
  python gp2040_10macros_aio.py --list-boards

  # Nur bestimmte Boards bauen
  python gp2040_10macros_aio.py --boards Pico PicoW Haute42 RP2040AdvancedBreakoutBoard

  # Alle Boards bauen, deren Name "Haute" oder "Pico" enthaelt
  python gp2040_10macros_aio.py --filter Haute Pico

  # Alle Boards bauen, bestimmte ausschliessen
  python gp2040_10macros_aio.py --exclude ExampleBoard

  # Bei fehlendem lokalen Pico SDK automatisch per Git laden lassen
  python gp2040_10macros_aio.py --fetch-pico-sdk

  # Nur Patch + Web-Build (ohne CMake)
  python gp2040_10macros_aio.py --web-only

  # Trockenlauf (zeigt erkannte Boards und geplante Patches ohne zu schreiben/bauen)
  python gp2040_10macros_aio.py --dry-run
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_REPO = "https://github.com/iSoHiGH83/GP2040-CE.git"
DEFAULT_DIR = "GP2040-CE"
DEFAULT_SLOTS = 10


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"


def supports_color() -> bool:
    return sys.stdout.isatty() and (
        os.name != "nt" or "WT_SESSION" in os.environ or "ANSICON" in os.environ
    )


USE_COLOR = supports_color()


def c(text: str, color: str) -> str:
    return f"{color}{text}{Color.RESET}" if USE_COLOR else text


def step(title: str) -> None:
    print(f"\n{c('==>', Color.CYAN + Color.BOLD)} {c(title, Color.BOLD)}")


def info(msg: str) -> None:
    print(f"    {msg}")


def ok(msg: str) -> None:
    print(f"  {c('[OK]', Color.GREEN)} {msg}")


def warn(msg: str) -> None:
    print(f"  {c('[WARN]', Color.YELLOW)} {msg}")


def err(msg: str) -> None:
    print(f"  {c('[ERR]', Color.RED)} {msg}", file=sys.stderr)


@dataclass
class BoardBuildResult:
    board: str
    success: bool
    duration_sec: float
    uf2_files: List[Path]
    error: str = ""


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> None:
    cmd_str = " ".join(str(x) for x in cmd)
    info(f"$ {cmd_str}")
    use_shell = os.name == "nt" and cmd[0] in ("npm", "npx", "yarn", "pnpm")
    result = subprocess.run(cmd, cwd=cwd, env=env, shell=use_shell)
    if result.returncode != 0:
        raise RuntimeError(f"Befehl fehlgeschlagen (Exit-Code {result.returncode}): {cmd_str}")


def check_tool(name: str, required: bool = True) -> bool:
    found = shutil.which(name) is not None
    if found:
        ok(f"Tool gefunden: {name}")
    elif required:
        err(f"Benoetigtes Tool nicht im PATH gefunden: {name}")
    else:
        warn(f"Optionales Tool nicht gefunden: {name}")
    return found


def clone_or_update_repo(repo_url: str, target_dir: Path, skip_clone: bool, dry_run: bool) -> None:
    step("1. Repository vorbereiten")
    if target_dir.exists() and (target_dir / ".git").exists():
        ok(f"Bestehendes Repository gefunden: {target_dir}")
        if not skip_clone and not dry_run:
            info("Initialisiere/aktualisiere Submodules...")
            run_cmd(["git", "submodule", "update", "--init", "--recursive"], cwd=target_dir)
        return

    if skip_clone:
        if not target_dir.exists():
            raise FileNotFoundError(f"Verzeichnis existiert nicht (--skip-clone aktiv): {target_dir}")
        ok(f"Nutze bestehendes Verzeichnis: {target_dir}")
        return

    if dry_run:
        info(f"[DRY-RUN] Wuerde klonen: git clone --recursive {repo_url} {target_dir}")
        return

    run_cmd(["git", "clone", "--recursive", repo_url, str(target_dir)])
    ok(f"Repository erfolgreich geklont nach {target_dir}")


def discover_boards(repo_dir: Path) -> List[str]:
    """Erkennt automatisch alle gueltigen GP2040-CE Board-Konfigurationen unter configs/."""
    configs_dir = repo_dir / "configs"
    if not configs_dir.exists():
        return []

    boards: List[str] = []
    for entry in sorted(configs_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if (entry / "BoardConfig.h").is_file():
            boards.append(entry.name)
    return boards


def select_boards(
    available_boards: List[str],
    requested: Optional[List[str]],
    filters: Optional[List[str]],
    excludes: Optional[List[str]],
) -> List[str]:
    """Filtert die verfuegbaren Boards anhand von --boards, --filter und --exclude."""
    if not available_boards:
        return []

    by_lower = {b.lower(): b for b in available_boards}

    if requested and not any(r.upper() == "ALL" for r in requested):
        selected: List[str] = []
        missing: List[str] = []
        for item in requested:
            key = item.strip().lower()
            if key in by_lower:
                board_name = by_lower[key]
                if board_name not in selected:
                    selected.append(board_name)
            else:
                missing.append(item)
        if missing:
            raise ValueError(
                f"Unbekannte Board-Konfiguration(en): {', '.join(missing)}. "
                f"Verfuegbare Boards mit --list-boards anzeigen."
            )
    else:
        selected = list(available_boards)

    if filters:
        filtered: List[str] = []
        for board in selected:
            bl = board.lower()
            if any(
                (fnmatch.fnmatch(bl, f.lower()) if any(ch in f for ch in "*?[") else f.lower() in bl)
                for f in filters
            ):
                filtered.append(board)
        selected = filtered

    if excludes:
        excl_lower = [e.lower() for e in excludes]
        selected = [
            b
            for b in selected
            if not any(
                (fnmatch.fnmatch(b.lower(), e) if any(ch in e for ch in "*?[") else b.lower() == e)
                for e in excl_lower
            )
        ]

    return selected


def patch_file(
    path: Path,
    patterns: List[Tuple[str, str, str]],
    dry_run: bool,
) -> List[str]:
    """Wendet Regex-Ersetzungen auf eine Datei an und liefert Beschreibungen aller Treffer."""
    if not path.exists():
        return []

    original = path.read_text(encoding="utf-8", errors="ignore")
    updated = original
    changes: List[str] = []

    for label, pattern, replacement in patterns:
        new_text, count = re.subn(pattern, replacement, updated)
        if count > 0 and new_text != updated:
            changes.append(f"{label} ({count}x)")
            updated = new_text

    if changes and updated != original:
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
        prefix = "[DRY-RUN] " if dry_run else ""
        ok(f"{prefix}Gepatcht: {path.as_posix()} -> {', '.join(changes)}")

    return changes


def patch_protobuf(repo_dir: Path, slots: int, max_inputs: Optional[int], dry_run: bool) -> int:
    step(f"2. Protobuf-Definitionen auf {slots} Makro-Slots erweitern")
    patched_count = 0
    proto_dir = repo_dir / "proto"
    if not proto_dir.exists():
        warn("Ordner proto/ nicht gefunden.")
        return 0

    for path in sorted(proto_dir.rglob("*")):
        if path.suffix not in (".options", ".proto"):
            continue
        patterns = [
            (
                f"MacroOptions.macroList max_count -> {slots}",
                r"(MacroOptions\.macroList\s+max_count\s*:\s*)\d+",
                rf"\g<1>{slots}",
            ),
            (
                f"macroList nanopb max_count -> {slots}",
                r"(macroList\s*=\s*\d+\s*\[\s*\(nanopb\)\.max_count\s*=\s*)\d+",
                rf"\g<1>{slots}",
            ),
        ]
        if max_inputs is not None:
            patterns.append(
                (
                    f"Macro.macroInputs max_count -> {max_inputs}",
                    r"(Macro\.macroInputs\s+max_count\s*:\s*)\d+",
                    rf"\g<1>{max_inputs}",
                )
            )
            patterns.append(
                (
                    f"macroInputs nanopb max_count -> {max_inputs}",
                    r"(macroInputs\s*=\s*\d+\s*\[\s*\(nanopb\)\.max_count\s*=\s*)\d+",
                    rf"\g<1>{max_inputs}",
                )
            )
        if patch_file(path, patterns, dry_run):
            patched_count += 1

    if patched_count == 0:
        warn("Keine Protobuf-Makro-Limits gefunden/geaendert (evtl. bereits auf Zielwert).")
    return patched_count


def patch_cpp(repo_dir: Path, slots: int, max_inputs: Optional[int], dry_run: bool) -> int:
    step(f"3. C++-Firmware-Dateien auf {slots} Makro-Slots erweitern")
    patched_count = 0
    search_dirs = [repo_dir / "headers", repo_dir / "src"]

    cpp_patterns = [
        (
            f"#define MAX_MACRO_LIMIT -> {slots}",
            r"(#define\s+MAX_MACRO_LIMIT\s+)\d+",
            rf"\g<1>{slots}",
        ),
        (
            f"#define MACRO_MAX_COUNT -> {slots}",
            r"(#define\s+MACRO_MAX_COUNT\s+)\d+",
            rf"\g<1>{slots}",
        ),
        (
            f"MAX_MACRO_LIMIT const -> {slots}",
            r"((?:constexpr|const)\s+\w+\s+MAX_MACRO_LIMIT\s*=\s*)\d+",
            rf"\g<1>{slots}",
        ),
    ]
    if max_inputs is not None:
        cpp_patterns.append(
            (
                f"#define MAX_MACRO_INPUT_LIMIT -> {max_inputs}",
                r"(#define\s+MAX_MACRO_INPUT_LIMIT\s+)\d+",
                rf"\g<1>{max_inputs}",
            )
        )

    for base in search_dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in (".h", ".hpp", ".cpp", ".c"):
                continue
            if patch_file(path, cpp_patterns, dry_run):
                patched_count += 1

    suspects = [
        repo_dir / "src" / "webconfig.cpp",
        repo_dir / "src" / "addons" / "input_macro.cpp",
        repo_dir / "src" / "config_utils.cpp",
        repo_dir / "headers" / "addons" / "input_macro.h",
    ]
    for path in suspects:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for idx, line in enumerate(text.splitlines(), start=1):
            if "macro" in line.lower() and re.search(r"\b6\b", line) and "MAX_MACRO" not in line:
                warn(
                    f"Moegliches hartcodiertes Limit '6' pruefen in {path.relative_to(repo_dir)}:{idx}: {line.strip()}"
                )

    if patched_count == 0:
        warn("Keine C++-Konstanten geaendert (evtl. bereits auf Zielwert oder anders benannt).")
    return patched_count


def patch_web_ui(repo_dir: Path, slots: int, max_inputs: Optional[int], dry_run: bool) -> int:
    step(f"4. Web-Configurator (www/src) auf {slots} Makro-Slots erweitern")
    www_src = repo_dir / "www" / "src"
    if not www_src.exists():
        warn("Verzeichnis www/src nicht gefunden.")
        return 0

    patched_count = 0
    macro_file_hints = ("macro", "Macro")

    for path in sorted(www_src.rglob("*")):
        if path.suffix not in (".ts", ".tsx", ".js", ".jsx"):
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        is_macro_file = any(h in path.name for h in macro_file_hints) or "macroList" in content

        if not is_macro_file:
            continue

        patterns: List[Tuple[str, str, str]] = [
            (
                f"MAX_MACRO_LIMIT/MAX_MACROS -> {slots}",
                r"((?:export\s+)?const\s+MAX_MACRO(?:S|_LIMIT|_COUNT)?\s*=\s*)\d+",
                rf"\g<1>{slots}",
            ),
            (
                f"macroList.length < 6 -> {slots}",
                r"(macroList\??\.length\s*(?:<|<=|>=|===|!==)\s*)6\b",
                rf"\g<1>{slots}",
            ),
            (
                f"macroList.slice(0, 6) -> {slots}",
                r"(macroList\.slice\(\s*0\s*,\s*)6(\s*\))",
                rf"\g<1>{slots}\g<2>",
            ),
            (
                f"Array(6) in Makro-Datei -> {slots}",
                r"(Array\(\s*)6(\s*\))",
                rf"\g<1>{slots}\g<2>",
            ),
            (
                f"Array.from length: 6 -> {slots}",
                r"(Array\.from\(\s*\{\s*length\s*:\s*)6(\s*\})",
                rf"\g<1>{slots}\g<2>",
            ),
        ]

        if max_inputs is not None:
            patterns.append(
                (
                    f"MAX_MACRO_INPUTS -> {max_inputs}",
                    r"((?:export\s+)?const\s+MAX_MACRO_INPUT(?:S|_LIMIT)?\s*=\s*)\d+",
                    rf"\g<1>{max_inputs}",
                )
            )

        if patch_file(path, patterns, dry_run):
            patched_count += 1

        if any(h in path.name for h in macro_file_hints):
            new_content = path.read_text(encoding="utf-8", errors="ignore") if not dry_run else content
            for idx, line in enumerate(new_content.splitlines(), start=1):
                if re.search(
                    r"\b(?:max|limit|length|count|slots)\b.*\b6\b|\b6\b.*\bmacro",
                    line,
                    re.IGNORECASE,
                ):
                    warn(f"Bitte pruefen in {path.relative_to(repo_dir)}:{idx}: {line.strip()}")

    if patched_count == 0:
        warn(
            "Keine direkte Makro-Limit-Konstante in www/src gefunden. "
            "Pruefe ggf. manuell mit: grep -rn 'macroList' www/src/"
        )
    return patched_count


def run_web_build(repo_dir: Path, dry_run: bool) -> None:
    step("5. Web-Build einmalig ausfuehren (www/)")
    www_dir = repo_dir / "www"
    if not www_dir.exists():
        raise FileNotFoundError(f"Ordner www/ nicht gefunden in {repo_dir}")

    if dry_run:
        info("[DRY-RUN] Wuerde ausfuehren: npm install && npm run build in www/")
        return

    if not check_tool("npm", required=True):
        raise RuntimeError("Node.js / npm muss installiert sein (empfohlen: Node 18 oder 20).")

    run_cmd(["npm", "install"], cwd=www_dir)
    run_cmd(["npm", "run", "build"], cwd=www_dir)

    build_out = www_dir / "build"
    if build_out.exists():
        ok(f"Web-Build erfolgreich erstellt: {build_out}")
    else:
        warn("npm run build abgeschlossen, aber www/build/ wurde nicht am Standardort gefunden.")


def build_single_board(
    repo_dir: Path,
    board: str,
    dist_dir: Path,
    jobs: int,
    fetch_pico_sdk: bool,
    keep_build_dirs: bool,
    copy_elf: bool,
    cmake_generator: Optional[str],
    dry_run: bool,
) -> BoardBuildResult:
    """Kompiliert ein einzelnes Board in einem isolierten build/<Board>-Verzeichnis."""
    start = time.monotonic()
    build_dir = repo_dir / "build" / board

    cmake_configure = [
        "cmake",
        "-B",
        str(build_dir),
        f"-DGP2040_BOARDCONFIG={board}",
        "-DSKIP_WEBBUILD=TRUE",
        "-DSKIP_SUBMODULES=TRUE",
    ]
    if fetch_pico_sdk:
        cmake_configure.append("-DPICO_SDK_FETCH_FROM_GIT=1")
    if cmake_generator:
        cmake_configure.extend(["-G", cmake_generator])

    if dry_run:
        info(f"[DRY-RUN] [{board}] {' '.join(cmake_configure)}")
        info(f"[DRY-RUN] [{board}] cmake --build {build_dir} -j {jobs}")
        return BoardBuildResult(board=board, success=True, duration_sec=0.0, uf2_files=[])

    try:
        # Sauberes Build-Verzeichnis pro Board verhindert CMakeCache-Konflikte zwischen Boards
        if build_dir.exists() and not keep_build_dirs:
            shutil.rmtree(build_dir, ignore_errors=True)

        env = os.environ.copy()
        env["GP2040_BOARDCONFIG"] = board

        run_cmd(cmake_configure, cwd=repo_dir, env=env)
        run_cmd(["cmake", "--build", str(build_dir), "-j", str(jobs)], cwd=repo_dir, env=env)

        dist_dir.mkdir(parents=True, exist_ok=True)
        found_uf2 = sorted(build_dir.glob("*.uf2"))
        if not found_uf2:
            # Fallback: rekursiv suchen, falls Generator Unterordner anlegt (z. B. Visual Studio)
            found_uf2 = sorted(build_dir.rglob("*.uf2"))

        copied_uf2: List[Path] = []
        for uf2 in found_uf2:
            target_name = uf2.name
            if board.lower() not in target_name.lower():
                target_name = f"{uf2.stem}_{board}{uf2.suffix}"
            dest = dist_dir / target_name
            shutil.copy2(uf2, dest)
            copied_uf2.append(dest)
            ok(f"[{board}] Firmware gesichert: {dest}")

        if copy_elf:
            for elf in sorted(build_dir.glob("*.elf")):
                elf_name = elf.name if board.lower() in elf.name.lower() else f"{elf.stem}_{board}{elf.suffix}"
                shutil.copy2(elf, dist_dir / elf_name)

        if not copied_uf2:
            raise RuntimeError(f"Keine .uf2-Datei im Build-Ordner fuer Board '{board}' gefunden.")

        if not keep_build_dirs:
            shutil.rmtree(build_dir, ignore_errors=True)

        return BoardBuildResult(
            board=board,
            success=True,
            duration_sec=time.monotonic() - start,
            uf2_files=copied_uf2,
        )

    except Exception as exc:
        if not keep_build_dirs and build_dir.exists():
            shutil.rmtree(build_dir, ignore_errors=True)
        return BoardBuildResult(
            board=board,
            success=False,
            duration_sec=time.monotonic() - start,
            uf2_files=[],
            error=str(exc),
        )


def run_all_board_builds(
    repo_dir: Path,
    boards: List[str],
    dist_dir: Path,
    jobs: int,
    fetch_pico_sdk: bool,
    keep_build_dirs: bool,
    copy_elf: bool,
    cmake_generator: Optional[str],
    fail_fast: bool,
    dry_run: bool,
) -> List[BoardBuildResult]:
    step(f"6. Firmware (.uf2) fuer {len(boards)} Board(s) kompilieren")
    if not boards:
        raise RuntimeError("Keine Boards zum Kompilieren ausgewaehlt oder in configs/ gefunden.")

    if not dry_run:
        if not check_tool("cmake", required=True):
            raise RuntimeError("CMake ist nicht installiert oder nicht im PATH.")
        pico_sdk = os.environ.get("PICO_SDK_PATH")
        if pico_sdk:
            ok(f"PICO_SDK_PATH = {pico_sdk}")
        elif fetch_pico_sdk:
            info("PICO_SDK_PATH nicht gesetzt - nutze PICO_SDK_FETCH_FROM_GIT=1.")
        else:
            warn(
                "PICO_SDK_PATH ist nicht gesetzt. Falls CMake abbricht, starte mit --fetch-pico-sdk "
                "oder setze PICO_SDK_PATH."
            )

    results: List[BoardBuildResult] = []
    total = len(boards)

    for idx, board in enumerate(boards, start=1):
        print(f"\n{c(f'--- [{idx}/{total}] Board: {board} ---', Color.CYAN + Color.BOLD)}")
        res = build_single_board(
            repo_dir=repo_dir,
            board=board,
            dist_dir=dist_dir,
            jobs=jobs,
            fetch_pico_sdk=fetch_pico_sdk,
            keep_build_dirs=keep_build_dirs,
            copy_elf=copy_elf,
            cmake_generator=cmake_generator,
            dry_run=dry_run,
        )
        results.append(res)
        if not res.success:
            err(f"[{board}] Fehlgeschlagen ({res.duration_sec:.1f}s): {res.error}")
            if fail_fast:
                break
        elif not dry_run:
            ok(f"[{board}] Fertig in {res.duration_sec:.1f}s")

    return results


def write_build_report(dist_dir: Path, results: List[BoardBuildResult], slots: int, dry_run: bool) -> None:
    step("7. Build-Zusammenfassung")
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    total_time = sum(r.duration_sec for r in results)

    info(f"Erfolgreich : {len(succeeded)} / {len(results)}")
    info(f"Fehlgeschlagen: {len(failed)}")
    if not dry_run:
        info(f"Gesamtdauer : {total_time:.1f}s")
        info(f"Ausgabeordner: {dist_dir}")

    if failed:
        warn("Fehlgeschlagene Boards:")
        for r in failed:
            warn(f"  - {r.board}: {r.error}")

    if dry_run or not results:
        return

    dist_dir.mkdir(parents=True, exist_ok=True)
    report_path = dist_dir / "build_report.txt"
    lines = [
        f"GP2040-CE Multi-Board Build Report ({slots} Makro-Slots)",
        "=" * 64,
        f"Erfolgreich   : {len(succeeded)} / {len(results)}",
        f"Fehlgeschlagen: {len(failed)}",
        f"Gesamtdauer   : {total_time:.1f}s",
        "",
        "[ERFOLGREICH]",
    ]
    for r in succeeded:
        files_str = ", ".join(p.name for p in r.uf2_files)
        lines.append(f"  + {r.board} ({r.duration_sec:.1f}s) -> {files_str}")

    if failed:
        lines.extend(["", "[FEHLGESCHLAGEN]"])
        for r in failed:
            lines.append(f"  - {r.board} ({r.duration_sec:.1f}s) -> {r.error}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"Build-Bericht gespeichert: {report_path}")


def show_git_diff_summary(repo_dir: Path) -> None:
    if (repo_dir / ".git").exists() and shutil.which("git"):
        step("Geaenderte Dateien (git status -s)")
        subprocess.run(["git", "status", "-s"], cwd=repo_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "All-in-One Skript: GP2040-CE auf 10 Makro-Slots erweitern, "
            "Web-Build ausfuehren und Firmware (.uf2) fuer ALLE Boards kompilieren."
        )
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Git-Repository-URL (Standard: {DEFAULT_REPO})")
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"Zielverzeichnis (Standard: {DEFAULT_DIR})")
    parser.add_argument("--out-dir", default=None, help="Zielordner fuer alle .uf2-Dateien (Standard: <dir>/dist)")
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS, help=f"Anzahl Makro-Slots (Standard: {DEFAULT_SLOTS})")
    parser.add_argument(
        "--max-inputs",
        type=int,
        default=None,
        help="Optional: Macro.macroInputs max_count anpassen (z. B. 20), falls RP2040-Flash-Konfig zu gross wird.",
    )
    parser.add_argument(
        "--boards",
        nargs="+",
        default=["ALL"],
        help="Zu bauende Boards (Standard: ALL fuer alle Boards in configs/, oder z. B. --boards Pico Haute42)",
    )
    parser.add_argument(
        "--board",
        default=None,
        help="Alias fuer ein einzelnes Board (z. B. --board Pico). Ueberschreibt --boards.",
    )
    parser.add_argument(
        "--filter",
        nargs="+",
        default=None,
        help="Boards nach Teilstring oder Wildcard filtern (z. B. --filter Pico Haute*)",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=None,
        help="Bestimmte Boards vom Multi-Board-Build ausschliessen",
    )
    parser.add_argument(
        "--list-boards",
        action="store_true",
        help="Alle gefundenen Boards aus configs/ auflisten und beenden",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="Parallele Build-Threads pro Board")
    parser.add_argument("--generator", default=None, help="Optionaler CMake-Generator (z. B. Ninja oder 'NMake Makefiles')")
    parser.add_argument(
        "--fetch-pico-sdk",
        action="store_true",
        help="Uebergibt -DPICO_SDK_FETCH_FROM_GIT=1 an CMake (falls PICO_SDK_PATH nicht lokal vorhanden)",
    )
    parser.add_argument(
        "--keep-build-dirs",
        action="store_true",
        help="Einzelne build/<Board>-Ordner nach dem Kopieren der .uf2 behalten (benoetigt deutlich mehr Speicherplatz)",
    )
    parser.add_argument("--copy-elf", action="store_true", help="Zusaetzlich .elf-Dateien nach dist/ kopieren")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Beim ersten fehlgeschlagenen Board sofort abbrechen statt restliche Boards weiterzubauen",
    )
    parser.add_argument("--web-only", action="store_true", help="Nur Patch + Web-Build durchfuehren (kein CMake Firmware-Build)")
    parser.add_argument("--patch-only", action="store_true", help="Nur klonen und patchen (kein npm/cmake Build)")
    parser.add_argument("--skip-clone", action="store_true", help="Kein git clone/submodule update ausfuehren")
    parser.add_argument("--skip-web-build", action="store_true", help="Web-Build ueberspringen (falls www/build bereits aktuell ist)")
    parser.add_argument("--dry-run", action="store_true", help="Aenderungen nur simulieren, nichts schreiben/bauen")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_dir = Path(args.dir).resolve()
    dist_dir = Path(args.out_dir).resolve() if args.out_dir else (target_dir / "dist")
    requested_boards = [args.board] if args.board else args.boards

    print(c("============================================================================", Color.CYAN))
    print(c(f"  GP2040-CE All-in-One Multi-Board Builder ({args.slots} Makro-Slots + Web-Build)", Color.BOLD))
    print(c("============================================================================", Color.CYAN))
    info(f"Repository : {args.repo}")
    info(f"Zielordner : {target_dir}")
    info(f"Output     : {dist_dir}")
    info(f"Makro-Slots: {args.slots}")
    if args.max_inputs is not None:
        info(f"Max Inputs : {args.max_inputs}")
    info(f"Boards     : {', '.join(requested_boards)}")

    try:
        if not args.skip_clone and not args.dry_run:
            if not check_tool("git", required=True):
                return 1

        clone_or_update_repo(args.repo, target_dir, args.skip_clone, args.dry_run)

        available_boards = discover_boards(target_dir) if target_dir.exists() else []
        selected_boards = (
            select_boards(available_boards, requested_boards, args.filter, args.exclude)
            if available_boards
            else []
        )

        if args.list_boards:
            step(f"Gefundene Boards in configs/ ({len(available_boards)} gesamt, {len(selected_boards)} ausgewaehlt)")
            for b in selected_boards:
                print(f"  - {b}")
            return 0

        if available_boards:
            ok(f"{len(available_boards)} Boards in configs/ erkannt -> {len(selected_boards)} fuer Build ausgewaehlt")
        elif args.dry_run:
            info("[DRY-RUN] Repository noch nicht lokal vorhanden - Board-Liste wird erst nach dem Klonen ermittelt.")

        if target_dir.exists():
            patch_protobuf(target_dir, args.slots, args.max_inputs, args.dry_run)
            patch_cpp(target_dir, args.slots, args.max_inputs, args.dry_run)
            patch_web_ui(target_dir, args.slots, args.max_inputs, args.dry_run)
        elif args.dry_run:
            info("[DRY-RUN] Repository noch nicht lokal vorhanden - Patch-Vorschau uebersprungen.")

        build_results: List[BoardBuildResult] = []
        if not args.patch_only:
            if not args.skip_web_build:
                run_web_build(target_dir, args.dry_run)
            if not args.web_only:
                if args.dry_run and not selected_boards:
                    info("[DRY-RUN] Wuerde alle Boards aus configs/*/BoardConfig.h einzeln per CMake bauen.")
                else:
                    build_results = run_all_board_builds(
                        repo_dir=target_dir,
                        boards=selected_boards,
                        dist_dir=dist_dir,
                        jobs=args.jobs,
                        fetch_pico_sdk=args.fetch_pico_sdk,
                        keep_build_dirs=args.keep_build_dirs,
                        copy_elf=args.copy_elf,
                        cmake_generator=args.generator,
                        fail_fast=args.fail_fast,
                        dry_run=args.dry_run,
                    )
                    write_build_report(dist_dir, build_results, args.slots, args.dry_run)

        if not args.dry_run and target_dir.exists():
            show_git_diff_summary(target_dir)

        failed_count = sum(1 for r in build_results if not r.success)
        if failed_count > 0:
            warn(f"Abgeschlossen mit {failed_count} fehlgeschlagenen Board-Build(s). Details siehe {dist_dir / 'build_report.txt'}.")
            return 2

        print(f"\n{c('Fertig!', Color.GREEN + Color.BOLD)} Alle ausgewaehlten Schritte wurden abgeschlossen.")
        if not args.web_only and not args.patch_only:
            info(f"Alle fertigen .uf2-Dateien liegen in: {dist_dir}")
            info("Hinweis: Nach dem Flashen im WebConfig (http://192.168.7.1) ggf. 'Reset Settings' ausfuehren,")
            info("         falls alte 6-Slot-Konfigurationsdaten im Flash liegen.")
        return 0

    except Exception as exc:
        err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
