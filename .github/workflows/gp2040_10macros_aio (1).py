#!/usr/bin/env python3
"""
GP2040-CE All-in-One Patch & Build Script (10 Makro-Slots + Web-Build + Firmware)
=================================================================================
Repository: https://github.com/iSoHiGH83/GP2040-CE.git

Dieses All-in-One-Skript automatisiert alle Schritte auf Windows, Linux und macOS:
  1. Klont das Repository inkl. Submodules (oder nutzt einen bestehenden Ordner)
  2. Erhoeht das Makro-Limit in Protobuf, C++-Firmware und Web-Configurator (www/) auf 10
     (optional auch Reduzierung von Macro.macroInputs fuer RP2040-Flash-Limits)
  3. Fuehrt den Web-Build (npm install && npm run build) durch
  4. Kompiliert optional direkt die Firmware (.uf2) per CMake

Verwendung:
  python gp2040_10macros_aio.py
  python gp2040_10macros_aio.py --board Pico --slots 10
  python gp2040_10macros_aio.py --web-only
  python gp2040_10macros_aio.py --slots 10 --max-inputs 20
  python gp2040_10macros_aio.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

DEFAULT_REPO = "https://github.com/iSoHiGH83/GP2040-CE.git"
DEFAULT_DIR = "GP2040-CE"
DEFAULT_SLOTS = 10
DEFAULT_BOARD = "Pico"


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"


def supports_color() -> bool:
    return sys.stdout.isatty() and (os.name != "nt" or "WT_SESSION" in os.environ or "ANSICON" in os.environ)


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


def run_cmd(cmd: List[str], cwd: Path | None = None, env: dict | None = None) -> None:
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


def patch_protobuf(repo_dir: Path, slots: int, max_inputs: int | None, dry_run: bool) -> int:
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


def patch_cpp(repo_dir: Path, slots: int, max_inputs: int | None, dry_run: bool) -> int:
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

    # Pruefe macro-relevante Dateien auf fest codierte 6er-Schleifen
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


def patch_web_ui(repo_dir: Path, slots: int, max_inputs: int | None, dry_run: bool) -> int:
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

        # Hinweis auf evtl. verbliebene hartcodierte 6er-Werte in Makro-UI-Dateien
        if any(h in path.name for h in macro_file_hints):
            new_content = path.read_text(encoding="utf-8", errors="ignore") if not dry_run else content
            for idx, line in enumerate(new_content.splitlines(), start=1):
                if re.search(r"\b(?:max|limit|length|count|slots)\b.*\b6\b|\b6\b.*\bmacro", line, re.IGNORECASE):
                    warn(
                        f"Bitte pruefen in {path.relative_to(repo_dir)}:{idx}: {line.strip()}"
                    )

    if patched_count == 0:
        warn(
            "Keine direkte Makro-Limit-Konstante in www/src gefunden. "
            "Pruefe ggf. manuell mit: grep -rn 'macroList' www/src/"
        )
    return patched_count


def run_web_build(repo_dir: Path, dry_run: bool) -> None:
    step("5. Web-Build ausfuehren (www/)")
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


def run_firmware_build(repo_dir: Path, board: str, jobs: int, dry_run: bool) -> None:
    step(f"6. Firmware (.uf2) fuer Board '{board}' kompilieren")
    if dry_run:
        info(f"[DRY-RUN] Wuerde ausfuehren: cmake -B build -DGP2040_BOARDCONFIG={board} -DSKIP_WEBBUILD=TRUE")
        info(f"[DRY-RUN] Wuerde ausfuehren: cmake --build build -j {jobs}")
        return

    if not check_tool("cmake", required=True):
        raise RuntimeError("CMake ist nicht installiert oder nicht im PATH.")

    pico_sdk = os.environ.get("PICO_SDK_PATH")
    if not pico_sdk:
        warn("Umgebungsvariable PICO_SDK_PATH ist nicht gesetzt. CMake versucht ggf. automatisches Fetch oder bricht ab.")
    else:
        ok(f"PICO_SDK_PATH = {pico_sdk}")

    build_dir = repo_dir / "build"
    cmake_configure = [
        "cmake",
        "-B",
        str(build_dir),
        f"-DGP2040_BOARDCONFIG={board}",
        "-DSKIP_WEBBUILD=TRUE",
    ]
    run_cmd(cmake_configure, cwd=repo_dir)
    run_cmd(["cmake", "--build", str(build_dir), "-j", str(jobs)], cwd=repo_dir)

    uf2_files = list(build_dir.glob("*.uf2"))
    if uf2_files:
        for uf2 in uf2_files:
            ok(f"Firmware erstellt: {uf2}")
    else:
        warn("CMake-Build abgeschlossen, aber keine .uf2-Datei direkt in build/ gefunden.")


def show_git_diff_summary(repo_dir: Path) -> None:
    if (repo_dir / ".git").exists() and shutil.which("git"):
        step("Geaenderte Dateien (git status -s)")
        subprocess.run(["git", "status", "-s"], cwd=repo_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="All-in-One Skript: GP2040-CE auf 10 Makro-Slots erweitern + Web-Build (+ Firmware)."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Git-Repository-URL (Standard: {DEFAULT_REPO})")
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"Zielverzeichnis (Standard: {DEFAULT_DIR})")
    parser.add_argument("--slots", type=int, default=DEFAULT_SLOTS, help=f"Anzahl Makro-Slots (Standard: {DEFAULT_SLOTS})")
    parser.add_argument(
        "--max-inputs",
        type=int,
        default=None,
        help="Optional: Macro.macroInputs max_count anpassen (z. B. 20), falls RP2040-Flash-Konfig zu gross wird.",
    )
    parser.add_argument("--board", default=DEFAULT_BOARD, help=f"GP2040_BOARDCONFIG fuer Firmware-Build (Standard: {DEFAULT_BOARD})")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="Parallele Build-Threads fuer CMake")
    parser.add_argument("--web-only", action="store_true", help="Nur Patch + Web-Build durchfuehren (kein CMake Firmware-Build)")
    parser.add_argument("--patch-only", action="store_true", help="Nur klonen und patchen (kein npm/cmake Build)")
    parser.add_argument("--skip-clone", action="store_true", help="Kein git clone/submodule update ausfuehren")
    parser.add_argument("--dry-run", action="store_true", help="Aenderungen nur simulieren, nichts schreiben/bauen")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_dir = Path(args.dir).resolve()

    print(c("======================================================================", Color.CYAN))
    print(c(f"  GP2040-CE All-in-One Builder ({args.slots} Makro-Slots + Web-Build)", Color.BOLD))
    print(c("======================================================================", Color.CYAN))
    info(f"Repository : {args.repo}")
    info(f"Zielordner : {target_dir}")
    info(f"Makro-Slots: {args.slots}")
    if args.max_inputs is not None:
        info(f"Max Inputs : {args.max_inputs}")
    info(f"Modus      : {'DRY-RUN' if args.dry_run else ('PATCH-ONLY' if args.patch_only else ('WEB-ONLY' if args.web_only else f'WEB + FIRMWARE ({args.board})'))}")

    try:
        if not args.skip_clone and not args.dry_run:
            if not check_tool("git", required=True):
                return 1

        clone_or_update_repo(args.repo, target_dir, args.skip_clone, args.dry_run)

        if target_dir.exists():
            patch_protobuf(target_dir, args.slots, args.max_inputs, args.dry_run)
            patch_cpp(target_dir, args.slots, args.max_inputs, args.dry_run)
            patch_web_ui(target_dir, args.slots, args.max_inputs, args.dry_run)
        elif args.dry_run:
            info("[DRY-RUN] Repository noch nicht lokal vorhanden - Patch-Vorschau uebersprungen.")

        if not args.patch_only:
            run_web_build(target_dir, args.dry_run)
            if not args.web_only:
                run_firmware_build(target_dir, args.board, args.jobs, args.dry_run)

        if not args.dry_run and target_dir.exists():
            show_git_diff_summary(target_dir)

        print(f"\n{c('Fertig!', Color.GREEN + Color.BOLD)} Alle ausgewaehlten Schritte wurden abgeschlossen.")
        if not args.web_only and not args.patch_only:
            info("Hinweis: Nach dem Flashen der .uf2 im WebConfig (http://192.168.7.1) ggf. 'Reset Settings' ausfuehren,")
            info("         falls alte 6-Slot-Konfigurationsdaten im Flash liegen.")
        return 0

    except Exception as exc:
        err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
