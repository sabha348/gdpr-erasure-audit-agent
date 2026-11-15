# Development notes

```bash
pip install -e '.[dev,llm]'
pre-commit install            # ruff, mypy, hygiene hooks
make lint test eval           # what CI runs (plus the docker smoke job: make up smoke)
```

Rules of the road:

- **The golden output is a contract.** `tests/golden/output.csv` pins all 518 answers byte for byte. A change
  in behaviour updates it in its own commit that says why, then the eval gate (`make eval`) must still pass.
- **Labels are never invented.** Labelled cases come only from `dataset/sample_output.csv` or from the
  repository owner (`eval/cases/custom/`, format in its README).
- **Schemas are generated.** After changing models or routes, run `make openapi` and regenerate
  `eval/schema/case.schema.json`; tests fail if either drifts.
- **Workflow code stays deterministic.** I/O, clocks and randomness belong in activities. If you change the
  workflow's command sequence, keep the replay test green, or version the workflow.
- **Secrets come only from the environment.** Never commit `.env`; settings keys are `SecretStr`.
- **Behind a TLS-intercepting proxy,** build with `make image CA_BUNDLE=/path/to/ca.crt`. The CA is a BuildKit
  secret and never ends up in a layer.
