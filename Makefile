.PHONY: init-db gen-b2b mutate-b2b gen-files gen-files-delta ingest-full ingest-incr dq reset test venv

PYTHON ?= python3

venv:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements.txt

init-db:
	$(PYTHON) -m src.common.init_db

gen-b2b:
	$(PYTHON) -m src.generators.generate_b2b

mutate-b2b:
	$(PYTHON) -m src.generators.mutate_b2b

gen-files:
	$(PYTHON) -m src.generators.generate_reseller_files

gen-files-delta:
	$(PYTHON) -m src.generators.generate_reseller_files --delta

ingest-full:
	$(PYTHON) -m src.ingestion.runner --mode full --sources b2b,reseller

ingest-incr:
	$(PYTHON) -m src.ingestion.runner --mode incremental --sources b2b,reseller

dq:
	$(PYTHON) -m src.quality.run_validations

reset:
	$(PYTHON) -m src.common.reset_db

test:
	$(PYTHON) -m pytest tests/ -v
