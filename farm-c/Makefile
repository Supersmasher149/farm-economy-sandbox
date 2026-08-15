# farm-c: the 11 agents ported to C, plus the shared economics/markets/
# inventory/contracts helpers they call, plus a bit-exact MT19937 RNG port
# (src/rng.c). See README.md for scope.
#
#   make fixtures     - regenerate tests/fixtures/agents.json from the real
#                        Python agents (requires the repo's Python venv; see
#                        ../tools/export_agent_fixtures.py)
#   make fixtures-rng - regenerate tests/fixtures/rng.json from CPython's
#                        random.Random (see ../tools/export_rng_fixtures.py)
#   make test         - build and run every fixture-driven parity test,
#                        under ASan+UBSan (docs/c-port-plan.md Section 9)
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

LIB_SRCS = $(wildcard src/*.c) $(wildcard src/agents/*.c)
LIB_OBJS = $(LIB_SRCS:.c=.o)

.PHONY: all test fixtures fixtures-rng clean

all: test

%.o: %.c
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) -c $< -o $@

tests/third_party/cJSON.o: tests/third_party/cJSON.c
	$(CC) $(CSTD) $(FP) $(SAN) $(INCLUDE) -c $< -o $@

tests/test_agents: tests/test_agents.c $(LIB_OBJS) tests/third_party/cJSON.o
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_rng: tests/test_rng.c src/rng.o tests/third_party/cJSON.o
	$(CC) $(CSTD) $(WARN) $(FP) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

fixtures:
	python3 ../tools/export_agent_fixtures.py

fixtures-rng:
	python3 ../tools/export_rng_fixtures.py

# `make test` alone (no Python needed) re-runs against whatever fixtures are
# already checked in; `make fixtures fixtures-rng test` regenerates them first.
test: tests/test_agents tests/test_rng
	./tests/test_agents
	./tests/test_rng

clean:
	rm -f $(LIB_OBJS) tests/third_party/cJSON.o tests/test_agents tests/test_rng
