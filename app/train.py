"""Run with: python -m app.train"""
import json
from .services.language_model import train_and_save


def main() -> None:
    artifact=train_and_save()
    print(json.dumps({"artifact":"app/model_artifacts/language_model.json","split_sizes":artifact["split_sizes"],"metrics":artifact["metrics"],"note":artifact["dataset_note"]},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
