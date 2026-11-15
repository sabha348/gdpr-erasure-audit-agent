# Developer shortcuts. Behind a TLS-intercepting proxy: make image CA_BUNDLE=/path/to/ca.crt
PY ?= python3
CA_BUNDLE ?=
IMAGE ?= erasure-audit:dev
BUILD_FLAGS = $(if $(CA_BUNDLE),--network host --build-arg HTTPS_PROXY --build-arg HTTP_PROXY --secret id=ca_bundle$(,)src=$(CA_BUNDLE),)
, := ,

.PHONY: install lint test eval image up down logs smoke openapi
install:          ## editable install with every extra
	$(PY) -m pip install -e ".[dev,llm]"
lint:
	ruff check src tests scripts
test:
	$(PY) -m pytest -q
eval:             ## labelled-case evaluation with leave-one-out CV and the CI regression gate
	erasure-audit eval --cv --min-verdict-accuracy 1.0 --min-step-f1 0.943
openapi:
	erasure-audit openapi > docs/openapi.json
image:
	docker build $(BUILD_FLAGS) -t $(IMAGE) .
up: image         ## Temporal dev server + worker + API (http://localhost:8000/docs, UI http://localhost:8233)
	docker compose up -d --no-build
down:
	docker compose down
logs:
	docker compose logs -f api worker
smoke:
	$(PY) scripts/smoke.py --base-url http://localhost:8000
