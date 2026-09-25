#!/usr/bin/env python3
"""Generate deterministic nested YAML translations for the compile benchmark."""

import argparse
import random
import shutil
from pathlib import Path


SCHEMAS = (
    "sales", "orders", "products", "customers", "sessions", "inventory",
    "checkouts", "discounts", "fulfillments", "payments", "refunds",
    "shipping", "taxes", "subscriptions", "marketing", "traffic",
)
SUFFIXES = (
    "display_name", "description", "short_description", "potential_uses",
    "helps_you",
)
WORDS = (
    "total gross net average median count rate ratio value amount quantity "
    "revenue conversion session visitor order product variant channel region "
    "currency discount refund tax shipping fulfillment payment subscription "
    "attributed first last touch window cohort returning new repeat"
).split()


def generate(out_dir: Path, locale_count: int, key_count: int, seed: int = 42) -> dict:
    rng = random.Random(seed)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    keys = []
    per_schema = max(1, key_count // (len(SCHEMAS) * len(SUFFIXES)))
    for schema in SCHEMAS:
        for index in range(per_schema):
            field = "_".join(rng.choice(WORDS) for _ in range(2)) + f"_{index}"
            for suffix in SUFFIXES:
                keys.append((schema, field, suffix))
                if len(keys) >= key_count:
                    break
            if len(keys) >= key_count:
                break
        if len(keys) >= key_count:
            break
    while len(keys) < key_count:
        keys.append(("misc", f"field_{len(keys)}", "display_name"))

    total_bytes = 0
    for locale in ["en"] + [f"l{index:02d}" for index in range(1, locale_count)]:
        tree = {}
        for schema, field, suffix in keys:
            word_count = 4 if suffix == "display_name" else rng.randint(8, 14)
            value = " ".join(rng.choice(WORDS) for _ in range(word_count)).capitalize()
            tree.setdefault(schema, {}).setdefault(field, {})[suffix] = f"{locale}: {value}"
        lines = []
        for schema in sorted(tree):
            lines.append(f"{schema}:")
            for field in sorted(tree[schema]):
                lines.append(f"  {field}:")
                for suffix in sorted(tree[schema][field]):
                    lines.append(f'    {suffix}: "{tree[schema][field][suffix]}"')
        body = "\n".join(lines) + "\n"
        (out_dir / f"{locale}.yml").write_text(body)
        total_bytes += len(body.encode())

    return {
        "locales": locale_count,
        "keys_per_locale": key_count,
        "translations": locale_count * key_count,
        "yaml_bytes": total_bytes,
        "first_key": ".".join(sorted(keys)[0]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("locales", type=int)
    parser.add_argument("keys", type=int)
    args = parser.parse_args()
    print(generate(args.output, args.locales, args.keys))
