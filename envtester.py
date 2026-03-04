#!/usr/bin/env python3
"""
envtester.py

Kombinierter Runner fuer zwei Betriebsmodi, gesteuert ueber RUNNER_MODE:

  query     – Pro Preset werden LightRAG-Query-Parameter in die .env geschrieben,
              LightRAG neugestartet, danach RAGChecker (evaluate).
              Am Ende: RAGChecker (compare) ueber alle Presets.

  ingestion – Pro Preset werden LightRAG-Ingestion-Parameter in die .env geschrieben,
              LightRAG neugestartet, danach RAGIngester gestartet.
              RAGIngester orchestriert selbst alle 14 Poisoning-Stufen + RAGChecker.
              Am Ende: RAGChecker (compare) ueber alle Presets.

Aufruf via docker compose:
  RUNNER_MODE=query     docker compose run --rm envtester --rebuild
  RUNNER_MODE=ingestion docker compose run --rm envtester --rebuild
"""

import argparse
import os
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
RUNNER_MODE         = os.environ.get("RUNNER_MODE", "query")   # query | ingestion

BASE_ENV            = "/opt/lightrag/.env"
BASE_ENV_BACKUP     = "/opt/lightrag/.env.bak"
LIGHTRAG_DIR        = "/opt/lightrag"
RAGCHECKER_DIR      = "/opt/ragchecker"
RAGINGESTER_DIR     = "/opt/ragingester"

LIGHTRAG_HOST       = "lightrag"
LIGHTRAG_PORT       = 9621
EMBEDDING_HOST      = "vllm-qwen3-vl-embedding"
EMBEDDING_PORT      = 9010
HEALTH_TIMEOUT      = 120
EMBED_TIMEOUT       = 600
RAGINGESTER_TIMEOUT = 7200

PRESETS_FILE = (
    "/opt/envtester/presets-ingestion.yml"
    if RUNNER_MODE == "ingestion"
    else "/opt/envtester/presets-query.yml"
)


# ============================================================
# Hilfsfunktionen
# ============================================================

def run(cmd, cwd=None, check=True):
    subprocess.run(cmd, shell=True, cwd=cwd, check=check)


def run_output(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# .env-Handling
# ============================================================

def write_env(preset_overrides: dict):
    """Ueberschreibt .env — Preset-Keys ersetzen, Rest bleibt. Cache immer aus."""
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

    for key, value in preset_overrides.items():
        if key not in replaced_keys:
            base_lines.append(f"{key}={value}")

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

    if RUNNER_MODE == "query":
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
    else:
        log("LightRAG bereit.")

    print()
    print("-- Aktive .env ------------------------------------------")
    for line in Path(BASE_ENV).read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            print(f"  {line}")
    print("-" * 58)
    print()


# ============================================================
# QUERY-Modus
# ============================================================

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


def run_query_presets(presets: list, run_group: str, dry_run: bool, skip_ragchecker: bool):
    for i, preset in enumerate(presets):
        label     = preset.get("label", f"preset_{i+1}")
        overrides = preset.get("env", {})

        print()
        print(f"-- Preset {i+1}/{len(presets)}: {label} " + "-" * max(0, 50 - len(label)))

        write_env(overrides)
        start_lightrag(dry_run=dry_run)

        if not skip_ragchecker:
            run_ragchecker(run_group, label, mode="evaluate", dry_run=dry_run)

    if not skip_ragchecker:
        print()
        print("-- Compare " + "-" * 50)
        run_ragchecker(run_group, run_group, mode="compare", dry_run=dry_run)


# ============================================================
# INGESTION-Modus
# ============================================================

def run_ragingester(run_group: str, label: str, dry_run=False):
    if dry_run:
        log(f"[dry_run] RAGIngester label={label} group={run_group}")
        return

    env_prefix = (
        f"RAGINGESTER_RUN_GROUP={run_group} "
        f"RAGINGESTER_RUN_LABEL={label} "
    )
    result = subprocess.run(
        f"{env_prefix}docker compose up --abort-on-container-exit --exit-code-from ragingester",
        shell=True, cwd=RAGINGESTER_DIR, timeout=RAGINGESTER_TIMEOUT
    )
    if result.returncode != 0:
        log(f"WARNUNG: RAGIngester exit code {result.returncode} fuer '{label}' — fahre fort.")

    subprocess.run(
        f"{env_prefix}docker compose down --remove-orphans",
        shell=True, cwd=RAGINGESTER_DIR, capture_output=True
    )


def run_ingestion_presets(presets: list, run_group: str, dry_run: bool, skip_compare: bool):
    for i, preset in enumerate(presets):
        label     = preset.get("label", f"preset_{i+1}")
        overrides = preset.get("env", {})

        print()
        print(f"-- Preset {i+1}/{len(presets)}: {label} " + "-" * max(0, 50 - len(label)))

        write_env(overrides)
        start_lightrag(dry_run=dry_run)
        run_ragingester(run_group, label, dry_run=dry_run)

    if not skip_compare:
        print()
        print("-- Compare " + "-" * 50)
        run_ragchecker(run_group, run_group, mode="compare", dry_run=dry_run)


# ============================================================
# Entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry_run",         action="store_true")
    parser.add_argument("--skip_ragchecker", action="store_true",
                        help="(query) RAGChecker-Schritte ueberspringen")
    parser.add_argument("--skip_compare",    action="store_true",
                        help="(ingestion) abschliessenden Compare ueberspringen")
    parser.add_argument("--rebuild",         action="store_true")
    args = parser.parse_args()

    if not Path(PRESETS_FILE).exists():
        log(f"FEHLER: Keine presets-Datei unter {PRESETS_FILE}")
        sys.exit(1)

    with open(PRESETS_FILE) as f:
        config = yaml.safe_load(f)

    presets   = config.get("presets", [])
    run_group = datetime.now().strftime("%Y%m%d_%H%M%S") + "_run"

    log(f"Modus: {RUNNER_MODE.upper()} | {len(presets)} Preset(s) | Run-Gruppe: {run_group}")

    if Path(BASE_ENV_BACKUP).exists():
        log(f"Vorhandenes Backup gefunden: {BASE_ENV_BACKUP} — wird wiederverwendet")
    else:
        shutil.copy2(BASE_ENV, BASE_ENV_BACKUP)
        log(f"Backup erstellt: {BASE_ENV_BACKUP}")

    if not args.dry_run and args.rebuild and RUNNER_MODE == "ingestion":
        log("Baue RAGIngester Image neu...")
        run("docker compose build --no-cache", cwd=RAGINGESTER_DIR)

    try:
        if RUNNER_MODE == "ingestion":
            run_ingestion_presets(presets, run_group, args.dry_run, args.skip_compare)
        else:
            run_query_presets(presets, run_group, args.dry_run, args.skip_ragchecker)
    finally:
        print()
        log("Cleanup: originale .env wiederherstellen und LightRAG neustarten...")
        shutil.copy2(BASE_ENV_BACKUP, BASE_ENV)
        Path(BASE_ENV_BACKUP).unlink()
        start_lightrag(dry_run=args.dry_run)
        log("Fertig.")


if __name__ == "__main__":
    main()