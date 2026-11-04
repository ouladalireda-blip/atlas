warning: in the working copy of '.gitignore', LF will be replaced by CRLF the next time Git touches it
[1mdiff --git a/.env.example b/.env.example[m
[1mindex 465d25b..ad8bcbb 100644[m
[1m--- a/.env.example[m
[1m+++ b/.env.example[m
[36m@@ -3,6 +3,9 @@[m
 # Niveau de journalisation : DEBUG, INFO, WARNING, ERROR[m
 ATLAS_LOG_LEVEL=INFO[m
 [m
[32m+[m[32m# Base SQLite locale (reçus, consommation, tâches) ; le dossier est créé si besoin[m
[32m+[m[32mATLAS_DATABASE_PATH=data/atlas.db[m
[32m+[m
 # Modèle de langage : openrouter (défaut, cloud) ou ollama (local)[m
 ATLAS_PROVIDER=openrouter[m
 # Obligatoire si ATLAS_PROVIDER=openrouter. Ne jamais commiter la vraie clé.[m
[1mdiff --git a/.gitignore b/.gitignore[m
[1mindex 716db94..54cea4b 100644[m
[1m--- a/.gitignore[m
[1m+++ b/.gitignore[m
[36m@@ -222,4 +222,9 @@[m [m__marimo__/[m
    legacy/[m
    .env[m
 data/[m
[31m-   [m
\ No newline at end of file[m
[32m+[m[41m   [m
[32m+[m[32m# Bases SQLite locales (reçus, tâches, consommation) : données personnelles.[m
[32m+[m[32m*.db[m
[32m+[m[32m*.db-wal[m
[32m+[m[32m*.db-shm[m
[32m+[m[32m*.db-journal[m
