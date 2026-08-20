# farm-c: the 11 agents ported to C, plus the shared economics/markets/
# inventory/contracts helpers they call, plus a bit-exact MT19937 RNG port
# (src/rng.c). See README.md for scope.
#
#   make fixtures         - regenerate tests/fixtures/agents.json from the
#                            real Python agents (requires the repo's Python
#                            venv; see ../tools/export_agent_fixtures.py)
#   make fixtures-rng     - regenerate tests/fixtures/rng.json from
#                            CPython's random.Random (see
#                            ../tools/export_rng_fixtures.py)
#   make fixtures-physics  - regenerate tests/fixtures/physics.json from the
#                            real simulation/crop_growth.py + weather.py
#                            (see ../tools/export_physics_fixtures.py)
#   make fixtures-mutation - regenerate tests/fixtures/mutation.json from
#                            the real simulation/actions.py, inventory.py,
#                            markets.py, processing.py, contracts.py (see
#                            ../tools/export_mutation_fixtures.py)
#   make test              - build and run every fixture-driven parity test,
#                            under ASan+UBSan (docs/c-port-plan.md Section 9)
#   make clean

CC ?= cc
CSTD = -std=c11
WARN = -Wall -Wextra
# Without this, a compiler is free to fuse `a + (b - a) * x`-shaped
# expressions (rng.c's rng_uniform, economy_rules.c, ...) into a single
# rounded FMA instead of two separately-rounded IEEE-754 operations --
# legal per C11's FP_CONTRACT default, and it silently produces 1-ulp
# divergences from Python (which never fuses). Same requirement as
# simulation/_fastplotmodule.c; see docs/c-port-plan.md Section 7.
FP = -ffp-contract=off
SAN = -fsanitize=address,undefined -fno-omit-frame-pointer
INCLUDE = -Iinclude
LIBS = -lm
# Emit a .d per object listing the headers it included, and -include them
# below. Without this the only header dependency make knows about is the one
# hand-written for cJSON, so editing include/*.h (or src/dashboard_js.h,
# which carries the whole dashboard renderer) leaves every stale .o in place
# and the next `make` quietly links the old code.
DEPFLAGS = -MMD -MP

LIB_SRCS = $(filter-out src/cJSON.c,$(wildcard src/*.c)) $(wildcard src/agents/*.c)
LIB_OBJS = $(LIB_SRCS:.c=.o)
CJSON_OBJ = src/cJSON.o
TEST_BINS = tests/test_agents tests/test_rng tests/test_physics tests/test_mutation \
            tests/test_config_loader tests/test_engine tests/test_runner tests/test_batch \
            tests/test_dashboard
DEPS = $(LIB_OBJS:.o=.d) $(CJSON_OBJ:.o=.d)

.PHONY: all test fixtures fixtures-rng fixtures-physics fixtures-mutation clean

all: test

%.o: %.c
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

src/cJSON.o: src/cJSON.c include/cJSON.h
	$(CC) $(CSTD) $(FP) $(SAN) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

tests/test_agents: tests/test_agents.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_rng: tests/test_rng.c src/rng.o $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_physics: tests/test_physics.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_mutation: tests/test_mutation.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

fixtures:
	python3 ../tools/export_agent_fixtures.py

fixtures-rng:
	python3 ../tools/export_rng_fixtures.py

fixtures-physics:
	python3 ../tools/export_physics_fixtures.py

fixtures-mutation:
	python3 ../tools/export_mutation_fixtures.py

# `make test` alone (no Python needed) re-runs against whatever fixtures are
# already checked in; `make fixtures fixtures-rng fixtures-physics
# fixtures-mutation test` regenerates them first.
tests/test_config_loader: tests/test_config_loader.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_engine: tests/test_engine.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

farm-c: main.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_runner: tests/test_runner.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_batch: tests/test_batch.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_dashboard: tests/test_dashboard.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

test: $(TEST_BINS) farm-c
	./tests/test_agents
	./tests/test_rng
	./tests/test_physics
	./tests/test_mutation
	./tests/test_config_loader
	./tests/test_engine
	./tests/test_runner
	./tests/test_batch
	./tests/test_dashboard
	sh tests/test_cli.sh

clean:
	rm -f $(LIB_OBJS) $(CJSON_OBJ) $(DEPS) $(TEST_BINS) farm-c

-include $(DEPS)
