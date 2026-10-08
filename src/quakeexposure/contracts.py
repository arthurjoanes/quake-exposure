"""Contratos JSON Schema de `contracts/`."""

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from quakeexposure.db import ROOT


def load(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text("utf-8"))
    return Draft202012Validator(schema)


def first_error(validator: Draft202012Validator, doc: Any) -> str | None:
    error = best_match(validator.iter_errors(doc))
    if error is None:
        return None
    # caminho e regra do schema, sem o valor recebido: a quarentena não guarda payload
    where = "/".join(str(p) for p in error.absolute_path) or "/"
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = [k for k in error.validator_value if k not in error.instance]
        return f"{where}: required {missing}"[:300]
    return f"{where}: {error.validator} {error.validator_value!r}"[:300]


EVENT = load("event")
SHAKEMAP = load("shakemap")
