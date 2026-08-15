# farm-c: the 11 agents ported to C, plus the shared economics/markets/
# inventory/contracts helpers they call. See README.md for scope.
#
#   make fixtures   - regenerate tests/fixtures/agents.json from the real
#                      Python agents (requires the repo's Python venv; see
#                      ../tools/export_agent_fixtures.py)
#   make test       - build and run the fixture-driven parity test, under
#                      ASan+UBSan (docs/c-port-plan.md Section 9)
#   make clean

CC ?= cc
CSTD = -std=c11
WARN = -Wall -Wextra
SAN = -fsanitize=address,undefined -fno-omit-frame-pointer
INCLUDE = -Iinclude
LIBS = -lm

LIB_SRCS = $(wildcard src/*.c) $(wildcard src/agents/*.c)
LIB_OBJS = $(LIB_SRCS:.c=.o)

.PHONY: all test fixtures clean

all: test

%.o: %.c
	$(CC) $(CSTD) $(WARN) $(SAN) $(INCLUDE) -c $< -o $@

tests/third_party/cJSON.o: tests/third_party/cJSON.c
	$(CC) $(CSTD) $(SAN) $(INCLUDE) -c $< -o $@

tests/test_agents: tests/test_agents.c $(LIB_OBJS) tests/third_party/cJSON.o
	$(CC) $(CSTD) $(WARN) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

fixtures:
	python3 ../tools/export_agent_fixtures.py

# `make test` alone (no Python needed) re-runs against whatever fixtures are
# already checked in; `make fixtures test` regenerates them first.
test: tests/test_agents
	./tests/test_agents

clean:
	rm -f $(LIB_OBJS) tests/third_party/cJSON.o tests/test_agents
