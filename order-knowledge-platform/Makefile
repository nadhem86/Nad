PY ?= python

.PHONY: install demo test ontology validate reconcile serve mcp docker-init docker-up

install:            ## dépendances
	$(PY) -m pip install -r requirements.txt
demo:               ## scénario complet (régénère les données fictives)
	$(PY) -m okp demo
test:               ## suite de tests
	$(PY) -m pytest
ontology:           ## régénère OWL + SHACL depuis le glossaire
	$(PY) -m okp build-ontology
validate:           ## validation SHACL du knowledge graph
	$(PY) -m okp validate
reconcile:          ## cohérence core ↔ knowledge graph
	$(PY) -m okp reconcile
serve:              ## API REST sur http://127.0.0.1:8000/docs
	$(PY) -m okp serve
mcp:                ## serveur MCP (stdio), identité = OKP_API_KEY
	$(PY) -m okp mcp
docker-init:
	docker compose --profile setup run --rm init
docker-up:
	docker compose up --build api
