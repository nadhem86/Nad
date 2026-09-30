"""Régénère ontology/ontology.ttl et ontology/shapes.ttl à partir de ontology/glossary.yaml."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.config import Settings  # noqa: E402
from hub.glossary import load_glossary  # noqa: E402
from hub.rdf import write_artifacts  # noqa: E402

if __name__ == "__main__":
    s = Settings()
    write_artifacts(load_glossary(s), s.glossary_path.parent)
    print("ontology.ttl et shapes.ttl régénérés depuis", s.glossary_path)
