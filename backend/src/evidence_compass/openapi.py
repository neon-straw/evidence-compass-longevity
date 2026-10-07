"""Печатает OpenAPI-схему API; из неё генерируются TS-типы клиента (ADR 0006)."""

import json

from evidence_compass.api import create_app

if __name__ == "__main__":
    print(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))
