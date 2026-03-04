# env-tester

Docker-Container der LightRAG-Konfigurationen per Preset sweept, jeweils LightRAG
neu startet und RAGChecker-Evaluierungen ausführt.

## Verzeichnisstruktur auf dem Host

```
/opt/
├── env-tester/          ← dieses Projekt
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── env_tester.py
│   ├── presets.yml
│   └── requirements.txt
├── lightrag/            ← gemountet (lesen/schreiben von .env)
└── ragchecker/          ← gemountet (docker compose run ragchecker)
```

## Manuell ausführen

```bash
cd /opt/env-tester

# Image bauen
docker compose build

# Normaler Run
docker compose run --rm env-tester

# Mit RAGChecker-Rebuild
docker compose run --rm env-tester --rebuild

# Dry-Run (kein docker, nur .env-Ausgabe)
docker compose run --rm env-tester --dry_run

# Ohne RAGChecker (nur .env wechseln + LightRAG neustarten)
docker compose run --rm env-tester --skip_ragchecker
```

## Git-Setup (einmalig)

```bash
cd /opt/env-tester

git init
git add .
git commit -m "feat: initial env-tester Docker project"

# Remote hinzufügen und pushen
git remote add origin git@github.com:<org>/<repo>.git
git branch -M main
git push -u origin main
```

## Deployment via Ansible

Die Ansible-Task `ansible/tasks/env_tester.yml` ersetzt die alte `ragchecker.yml`.
Sie kopiert alle Dateien nach `/opt/env-tester/`, klont RAGChecker und führt den
Container-Run aus.

Ansible-Rolle einbinden:
```yaml
- import_tasks: tasks/env_tester.yml
```

Benötigte Variablen (aus `main.yml`/`defaults`):
- `ragchecker_base_dir` — z.B. `/opt/ragchecker`
