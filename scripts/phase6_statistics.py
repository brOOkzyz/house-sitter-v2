#!/usr/bin/env python3
"""Small deterministic paired-statistics helper for the locked Phase 6 analysis."""
from __future__ import annotations

import argparse
from math import comb
import json


def paired_binary_effect(full: list[bool], counterfactual: list[bool]) -> dict[str, float | int]:
    if not full or len(full) != len(counterfactual):
        raise ValueError("Paired binary inputs must be non-empty and aligned.")
    full_only = sum(left and not right for left, right in zip(full, counterfactual))
    counterfactual_only = sum(not left and right for left, right in zip(full, counterfactual))
    discordant = full_only + counterfactual_only
    tail = sum(comb(discordant, value) for value in range(min(full_only, counterfactual_only) + 1)) / (2**discordant) if discordant else 0.5
    return {"n_pairs": len(full), "full_only": full_only, "counterfactual_only": counterfactual_only, "absolute_difference_pp": round(100 * (sum(counterfactual) - sum(full)) / len(full), 12), "exact_mcnemar_pvalue": min(1.0, 2 * tail) if discordant else 1.0}


def self_test() -> dict[str, float | int | str]:
    result = paired_binary_effect([True, False, False, False], [True, True, True, False])
    assert result == {"n_pairs": 4, "full_only": 0, "counterfactual_only": 2, "absolute_difference_pp": 50.0, "exact_mcnemar_pvalue": 0.5}
    return {"status": "passed", **result}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 6 paired McNemar/effect-size self-check.")
    parser.add_argument("command", choices=("self-test",))
    args = parser.parse_args()
    print(json.dumps(self_test(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
