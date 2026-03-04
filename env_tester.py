#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.stdout.reconfigure(line_buffering=True)

# ============================================================
# Konfiguration
# ============================================================
BASE_ENV            = "/opt/lightrag/.env"
BASE_ENV_BACKUP     = "/opt/lightrag/.env.bak"
LIGHTRAG_DIR        = "/opt/lightrag"
RAGCHECKER_DIR      = "/opt/ragchecker"
PRESETS_FILE        = "/opt/envtester/presets.yml"
LIGHTRAG_HOST       = "lightrag"
LIGHTRAG_PORT       = 9621
EMBEDDING_HOST      = "vllm-qwen3-vl-embedding"
EMBEDDING_PORT      = 9010
HEALTH_TIMEOUT      = 120
EMBED_TIMEOUT       = 600


def run(cmd, cwd=None, check=True):
    subprocess.run(cmd, shell=True, cwd=cwd, check=check)


def run_output(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def write_env(preset_overrides: dict):
    """Überschreibt .env direkt — Preset-Keys ersetzen, Rest bleibt."""
    base_lines = []
    replaced_keys = set()

    for line in Path(BASE_ENV_BACKUP).read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in preset_overrides:
                base_lines.append(f"{key}={preset_overrides[key]}")
                replaced_keys.add(key)
                continue
        base_lines.append(line)

    # Keys die noch nicht in der .env waren anhängen
    for key, value in preset_overrides.items():
        if key not in replaced_keys:
            base_lines.append(f"{key}={value}")

    # Caching immer deaktivieren fuer Testruns
    final_lines = []
    cache_set = False
    for line in base_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == "ENABLE_LLM_CACHE":
            final_lines.append("ENABLE_LLM_CACHE=false")
            cache_set = True
        else:
            final_lines.append(line)
    if not cache_set:
        final_lines.append("ENABLE_LLM_CACHE=false")
    Path(BASE_ENV).write_text("\n".join(final_lines) + "\n")


def start_lightrag(dry_run=False):
    if dry_run:
        print()
        print("-- Aktive .env (dry_run) --------------------------------")
        for line in Path(BASE_ENV).read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                print(f"  {line}")
        print("-" * 58)
        print()
        return

    run("docker compose down", cwd=LIGHTRAG_DIR)
    run("docker compose up -d", cwd=LIGHTRAG_DIR)

    log("Warte auf LightRAG...")
    waited = 0
    while waited < HEALTH_TIMEOUT:
        time.sleep(5)
        waited += 5
        ok = subprocess.run(
            f"curl -sf http://{LIGHTRAG_HOST}:{LIGHTRAG_PORT}/health",
            shell=True, capture_output=True
        ).returncode == 0
        if ok:
            break
    else:
        log(f"FEHLER: LightRAG nicht erreichbar nach {HEALTH_TIMEOUT}s.")
        run("docker compose logs --tail 30", cwd=LIGHTRAG_DIR, check=False)
        sys.exit(1)

    log("LightRAG bereit. Warte auf Embedding-Service...")
    waited = 0
    while waited < EMBED_TIMEOUT:
        status = run_output(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"http://{EMBEDDING_HOST}:{EMBEDDING_PORT}/v1/models"
        )
        if status == "200":
            break
        time.sleep(10)
        waited += 10
    else:
        log(f"FEHLER: Embedding-Service nicht erreichbar nach {EMBED_TIMEOUT}s.")
        sys.exit(1)

    log("Embedding-Service bereit.")

    print()
    print("-- Aktive .env ------------------------------------------")
    for line in Path(BASE_ENV).read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            print(f"  {line}")
    print("-" * 58)
    print()


def run_ragchecker(run_group: str, label: str, mode: str, dry_run=False):
    if dry_run:
        log(f"[dry_run] RAGChecker mode={mode} label={label} group={run_group}")
        return

    cmd = (
        f"RAGCHECKER_RUN_GROUP={run_group} "
        f"RAGCHECKER_RUN_LABEL={label} "
        f"RAGCHECKER_MODE={mode} "
        f"docker compose run --rm ragchecker"
    )
    result = subprocess.run(cmd, shell=True, cwd=RAGCHECKER_DIR)
    if result.returncode != 0:
        log(f"WARNUNG: RAGChecker exit code {result.returncode} — fahre fort.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry_run",         action="store_true")
    parser.add_argument("--skip_ragchecker", action="store_true")
    parser.add_argument("--rebuild",         action="store_true")
    args = parser.parse_args()

    if not Path(PRESETS_FILE).exists():
        log(f"FEHLER: Keine presets.yml unter {PRESETS_FILE}")
        sys.exit(1)

    with open(PRESETS_FILE) as f:
        config = yaml.safe_load(f)

    presets = config.get("presets", [])
    run_group = datetime.now().strftime("%Y%m%d_%H%M%S") + "_run"

    log(f"{len(presets)} Preset(s) | Run-Gruppe: {run_group}")

    # Backup der originalen .env — wird am Ende wiederhergestellt
    # Existiert bereits ein Backup (von gecrashetem Run), dieses behalten
    if Path(BASE_ENV_BACKUP).exists():
        log(f"Vorhandenes Backup gefunden: {BASE_ENV_BACKUP} -- wird wiederverwendet")
    else:
        shutil.copy2(BASE_ENV, BASE_ENV_BACKUP)
        log(f"Backup erstellt: {BASE_ENV_BACKUP}")

    if not args.dry_run and args.rebuild:
        log("Baue RAGChecker Image neu...")
        run("docker compose build --no-cache", cwd=RAGCHECKER_DIR)

    try:
        for i, preset in enumerate(presets):
            label = preset.get("label", f"preset_{i+1}")
            overrides = preset.get("env", {})

            print()
            print(f"-- Preset {i+1}/{len(presets)}: {label} " + "-" * max(0, 50 - len(label)))

            write_env(overrides)
            start_lightrag(dry_run=args.dry_run)

            if not args.skip_ragchecker:
                run_ragchecker(run_group, label, mode="evaluate", dry_run=args.dry_run)

        if not args.skip_ragchecker:
            print()
            print("-- Compare " + "-" * 50)
            run_ragchecker(run_group, run_group, mode="compare", dry_run=args.dry_run)

    finally:
        # Immer wiederherstellen — auch bei Fehler oder Ctrl+C
        print()
        log("Cleanup: originale .env wiederherstellen und LightRAG neustarten...")
        shutil.copy2(BASE_ENV_BACKUP, BASE_ENV)
        Path(BASE_ENV_BACKUP).unlink()
        start_lightrag(dry_run=args.dry_run)
        log("Fertig.")


if __name__ == "__main__":
    main()
