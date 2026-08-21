#!/bin/sh
set -eu

summary=$(./farm-c single --seed 42)
case "$summary" in
    *"strategy: profit_optimizer"*"actual_seed: 42"*"days_simulated:"*"bankrupt:"*) ;;
    *) echo "CLI summary is missing required fields" >&2; exit 1 ;;
esac

history=$(./farm-c single --strategy fast_seller --seed 42 --verbose)
case "$history" in
    *"day 1:"*"strategy: fast_seller"*) ;;
    *) echo "CLI verbose output is missing day history" >&2; exit 1 ;;
esac

if ./farm-c single --strategy missing_strategy >/dev/null 2>&1; then
    echo "unknown strategy unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c single --invalid-option >/dev/null 2>&1; then
    echo "invalid argument unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c single --config /definitely/missing/farm-config >/dev/null 2>&1; then
    echo "missing config unexpectedly succeeded" >&2
    exit 1
fi

batch_summary=$(./farm-c batch --runs 2 --seed 42 --strategy fast_seller --strategy profit_optimizer)
case "$batch_summary" in
    *"base_seed: 42"*"total_runs: 4"*"fast_seller"*"profit_optimizer"*) ;;
    *) echo "batch summary is missing required fields" >&2; exit 1 ;;
esac

csv_path=$(mktemp)
./farm-c batch --runs 2 --seed 42 --strategy fast_seller --csv "$csv_path" >/dev/null
csv_rows=$(wc -l <"$csv_path")
rm -f "$csv_path"
if [ "$csv_rows" -ne 3 ]; then
    echo "batch --csv did not write the expected header + 2 rows" >&2
    exit 1
fi

html_path=$(mktemp)
./farm-c batch --runs 2 --seed 42 --strategy fast_seller --html "$html_path" >/dev/null
html=$(cat "$html_path")
case "$html" in
    *"<!doctype html>"*"var RUNS=["*"var SUMMARY=["*"</html>"*) ;;
    *) echo "batch --html did not write a complete page" >&2; rm -f "$html_path"; exit 1 ;;
esac
rm -f "$html_path"

# --html must not disturb the run stream: same seed, same CSV, byte for byte.
# The dashboard reads BatchRunResults from the same callback the CSV writer
# does, so a change in ordering or in RNG consumption would show up here.
plain_csv=$(mktemp)
with_html_csv=$(mktemp)
throwaway_html=$(mktemp)
./farm-c batch --runs 3 --seed 42 --csv "$plain_csv" >/dev/null
./farm-c batch --runs 3 --seed 42 --csv "$with_html_csv" --html "$throwaway_html" >/dev/null
if ! cmp -s "$plain_csv" "$with_html_csv"; then
    echo "--html changed batch results for a fixed seed" >&2
    rm -f "$plain_csv" "$with_html_csv" "$throwaway_html"
    exit 1
fi
rm -f "$plain_csv" "$with_html_csv" "$throwaway_html"

if ./farm-c batch --runs 1 --html /definitely/missing/dir/out.html >/dev/null 2>&1; then
    echo "unwritable --html path unexpectedly succeeded" >&2
    exit 1
fi

if ./farm-c batch --strategy missing_strategy --runs 1 >/dev/null 2>&1; then
    echo "unknown batch strategy unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c batch --runs 0 >/dev/null 2>&1; then
    echo "non-positive --runs unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c batch --runs 1 --start-money -1 >/dev/null 2>&1; then
    echo "negative --start-money unexpectedly succeeded" >&2
    exit 1
fi
if ./farm-c batch --runs 1 --days 0 >/dev/null 2>&1; then
    echo "non-positive --days unexpectedly succeeded" >&2
    exit 1
fi

config_variant=$(mktemp -d)
cp ../config/*.json "$config_variant/"
printf '%s\n' '{"base_capacity":1}' >"$config_variant/processing.json"
printf '%s\n' '[{"id":"local","items":["quickweed"],"quantity_range":[1,2],"deadline_days":1}]' >"$config_variant/buyers.json"
printf '%s\n' '{}' >"$config_variant/soil.json"
if ! ./farm-c single --config "$config_variant" --strategy fast_seller --seed 42 >/dev/null; then
    echo "valid omitted processing/soil/buyer fields were rejected" >&2
    rm -rf "$config_variant"
    exit 1
fi
printf '%s\n' '[]' >"$config_variant/crops.json"
if ./farm-c single --config "$config_variant" --strategy fast_seller --seed 42 >/dev/null 2>&1; then
    echo "empty crop catalog unexpectedly loaded" >&2
    rm -rf "$config_variant"
    exit 1
fi
cp ../config/crops.json "$config_variant/crops.json"
printf '%s\n' '{"base_capacity":1,"products":[{"id":"dry","name":"Dry","processed_base_price":1}],"recipes":[{"id":"r","input_item_id":"quickweed","output_item_id":"dry","input_quantity":1,"output_quantity":1,"processing_days":1,"cost":0}]}' >"$config_variant/processing.json"
if ./farm-c single --config "$config_variant" --strategy fast_seller --seed 42 >/dev/null 2>&1; then
    echo "recipe without shelf_life_days unexpectedly loaded" >&2
    rm -rf "$config_variant"
    exit 1
fi
printf '%s\n' '{"default_variation":0.12,"minimum_supply_multiplier":0.95,"supply_decay":0.75,"channels":[{"id":"wholesale","name":"Wholesale","price_multiplier":1.0,"daily_capacity":1000}]}' >"$config_variant/markets.json"
if ./farm-c single --config "$config_variant" --strategy fast_seller --seed 42 >/dev/null 2>&1; then
    echo "configuration without a spot channel unexpectedly loaded" >&2
    rm -rf "$config_variant"
    exit 1
fi
rm -rf "$config_variant"

if ./farm-c single --seed ' -1' >/dev/null 2>&1; then
    echo "whitespace-prefixed negative seed unexpectedly succeeded" >&2
    exit 1
fi
max_seed_summary=$(./farm-c single --seed 18446744073709551615)
case "$max_seed_summary" in
    *"actual_seed: 18446744073709551615"*) ;;
    *) echo "maximum uint64 seed was not preserved" >&2; exit 1 ;;
esac
if ./farm-c batch >/dev/null 2>&1; then
    echo "missing --runs unexpectedly succeeded" >&2
    exit 1
fi
printf '%s\n' "CLI tests passed"
