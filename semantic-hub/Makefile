PY ?= python3

install:        ## installe les dépendances
	$(PY) -m pip install -r requirements-dev.txt
ontology:       ## régénère ontology.ttl et shapes.ttl depuis le glossaire
	$(PY) scripts/build_ontology.py
demo:           ## démonstration complète (base neuve var/demo.sqlite)
	$(PY) scripts/demo.py
test:           ## tests d'acceptation
	$(PY) -m pytest -q
ingest-demo:    ## importe le jeu de démonstration dans NAD_DB_PATH
	$(PY) -m hub.cli ingest-demo
validate:       ## validation SHACL
	$(PY) -m hub.cli validate
serve:          ## API REST sur :8000 (docs : /docs)
	$(PY) -m hub.cli serve
mcp:            ## serveur MCP stdio
	$(PY) -m hub.cli mcp
docker-up:
	docker compose up --build -d api
docker-test:
	docker compose --profile test run --rm tests

.PHONY: install ontology demo test ingest-demo validate serve mcp docker-up docker-test
