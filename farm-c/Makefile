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
#                            under ASan+UBSan (docs/c-port-plan.md Section 9),
#                            then the committed golden-replay baseline
#   make golden-check      - diff every (strategy, seed) combo against
#                            tests/golden_baseline.json (no Python needed)
#   make golden-capture    - re-record that baseline. Only after verifying
#                            the change against Python with the c-parity
#                            skill -- see include/golden.h.
#   make golden-check-profile
#                          - the same baseline against the -O2 build, which
#                            no other target compiles. Catches optimizer-
#                            induced FP drift (FMA contraction) that the
#                            -O0 sanitized suite cannot see.
#   make release            - build build/farm-c-release: -O2, no sanitizers,
#                            debug symbols kept. THIS is the binary to run
#                            real work on. The default `farm-c` carries
#                            ASan+UBSan at -O0 and is ~20x slower for
#                            byte-identical output -- use it to test, not to
#                            simulate.
#   make profile            - alias for `release`. Same binary; the name is
#                            kept because profiling CPU time/memory
#                            (Instruments, `sample`, `/usr/bin/time -l`) must
#                            happen against this build and never against
#                            ASan+UBSan `farm-c`.
#   make WITH_MEM0=1 farm-c - also link src/mem0_client.c against libcurl, so
#                            `single`/`batch --mem0` can record run/batch
#                            summaries to the Mem0 Platform. Off by default:
#                            plain `make`/`make test`/`make farm-c` link no
#                            HTTP client and need no libcurl. See README.md's
#                            "Recording run/batch summaries to Mem0".
#   make clean

CC ?= cc
CSTD = -std=c11
WARN = -Wall -Wextra
# src/cJSON.c is vendored upstream code, so it is compiled without $(WARN) --
# we do not get to fix its style, and warnings we will never act on bury the
# ones we would. It still trips clang's on-by-default deprecation warning six
# times (sprintf, deprecated in the macOS SDK), which is the same noise for
# the same reason, so silence that too rather than leaving six lines of
# unactionable output in every clean build. Applies to the vendored rules
# only; farm-c's own sources stay -Wall -Wextra clean.
VENDOR_WARN = -Wno-deprecated-declarations
# Without this, a compiler is free to fuse `a + (b - a) * x`-shaped
# expressions (rng.c's rng_uniform, economy_rules.c, ...) into a single
# rounded FMA instead of two separately-rounded IEEE-754 operations --
# legal per C11's FP_CONTRACT default, and it silently produces 1-ulp
# divergences from Python (which never fuses). Same requirement as
# simulation/_fastplotmodule.c; see docs/c-port-plan.md Section 7.
FP = -ffp-contract=off
# POSIX threads, for src/batch.c's parallel batch path. -pthread is both a
# compile flag (it defines the reentrancy macros) and a link flag (it pulls
# in the threading runtime; a no-op on macOS, where pthreads live in
# libSystem), so it rides along on every rule -- exactly like $(FP), and for
# the same reason: a rule that quietly loses it is a rule whose objects do
# not match the rest of the build.
THREADS = -pthread
SAN = -fsanitize=address,undefined -fno-omit-frame-pointer
# Debug symbols. Doesn't change codegen/output (harmless for determinism),
# just lets lldb/gdb and Instruments map back to source and line numbers.
DBG = -g
# WITH_MEM0=1 opts into src/mem0_client.c's real implementation (libcurl,
# POSTs to the Mem0 Platform) instead of its always-fails stub -- see that
# file's header comment. Off by default so `make`/`make test`/CI need no
# libcurl and no network, the same "optional accelerator, never silently
# required" rule ../CLAUDE.md states for _fastplotmodule.c and the Cython
# build. $(MEM0_DEFINE) rides along with $(INCLUDE) into every compile line
# below rather than threading a separate variable through each rule; $(LIBS)
# picks up -lcurl the same way, and only when WITH_MEM0 is set.
WITH_MEM0 ?=
MEM0_DEFINE = $(if $(WITH_MEM0),-DFARM_WITH_MEM0)
INCLUDE = -Iinclude $(MEM0_DEFINE)
LIBS = -lm $(if $(WITH_MEM0),-lcurl)
# Emit a .d per object listing the headers it included, and -include them
# below. Without this the only header dependency make knows about is the one
# hand-written for cJSON, so editing include/*.h (or src/dashboard_js.h,
# which carries the whole dashboard renderer) leaves every stale .o in place
# and the next `make` quietly links the old code.
DEPFLAGS = -MMD -MP

# .o/.d go under here (mirroring src/'s layout, e.g. src/agents/foo.c ->
# build/agents/foo.o) instead of next to the .c files, so `git status` and
# `find src` stay clean of build output.
BUILD_DIR = build

LIB_SRCS = $(filter-out src/cJSON.c,$(wildcard src/*.c)) $(wildcard src/agents/*.c)
LIB_OBJS = $(patsubst src/%.c,$(BUILD_DIR)/%.o,$(LIB_SRCS))
CJSON_OBJ = $(BUILD_DIR)/cJSON.o
TEST_BINS = tests/test_pyfloat tests/test_agents tests/test_rng tests/test_physics \
            tests/test_mutation \
            tests/test_config_loader tests/test_engine tests/test_runner tests/test_batch \
            tests/test_dashboard tests/test_trajectory tests/test_reporting \
            tests/test_config_invalid tests/test_progress
DEPS = $(LIB_OBJS:.o=.d) $(CJSON_OBJ:.o=.d)

.PHONY: all test fixtures fixtures-rng fixtures-physics fixtures-mutation clean profile \
        release golden-check golden-capture golden-check-profile

all: test

$(BUILD_DIR)/%.o: src/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

$(BUILD_DIR)/cJSON.o: src/cJSON.c include/cJSON.h
	@mkdir -p $(dir $@)
	$(CC) $(CSTD) $(VENDOR_WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

# Only pyfloat.o: this is a differential test of one function against its
# own libc reference, and linking the whole simulator would let an unrelated
# link error mask it.
tests/test_pyfloat: tests/test_pyfloat.c $(BUILD_DIR)/pyfloat.o
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_agents: tests/test_agents.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_rng: tests/test_rng.c $(BUILD_DIR)/rng.o $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_physics: tests/test_physics.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_mutation: tests/test_mutation.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

# The golden baseline is checked by `make test`; capture is deliberately a
# separate target you have to ask for, because re-recording it is how an
# unintended behavior change gets silently blessed. See include/golden.h.
golden-check: farm-c
	./farm-c golden check

golden-capture: farm-c
	./farm-c golden capture

# The same baseline, re-run against the -O2 build. `make test` only ever
# exercises the -O0 sanitized binary, so nothing else here compiles the
# simulator with the optimizer on: every optimization that can legally
# change floating-point results -- FMA contraction above all -- is invisible
# to the rest of the suite. That is exactly the drift $(FP) exists to
# prevent, and a build rule that loses $(FP) (or gains -ffast-math) still
# passes every other target. Verified to have teeth: rebuilding the profile
# objects with -ffp-contract=fast diverges all 44 combos at 1 ulp.
golden-check-profile: $(BUILD_DIR)/farm-c-release
	./$(BUILD_DIR)/farm-c-release golden check

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
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_engine: tests/test_engine.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

farm-c: main.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_runner: tests/test_runner.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_batch: tests/test_batch.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_dashboard: tests/test_dashboard.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_trajectory: tests/test_trajectory.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_reporting: tests/test_reporting.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_config_invalid: tests/test_config_invalid.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

tests/test_progress: tests/test_progress.c $(LIB_OBJS) $(CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(DBG) $(SAN) $(INCLUDE) $^ -o $@ $(LIBS)

test: $(TEST_BINS) farm-c
	./tests/test_pyfloat
	./tests/test_agents
	./tests/test_rng
	./tests/test_physics
	./tests/test_mutation
	./tests/test_config_loader
	./tests/test_engine
	./tests/test_runner
	./tests/test_batch
	./tests/test_dashboard
	./tests/test_trajectory
	./tests/test_reporting
	./tests/test_config_invalid
	./tests/test_progress
	sh tests/test_cli.sh
	./farm-c golden check
	$(MAKE) golden-check-profile

clean:
	rm -rf $(BUILD_DIR)
	rm -f $(TEST_BINS) farm-c
	# Debug-symbol bundles the linker leaves beside each binary under -g.
	rm -rf farm-c.dSYM $(addsuffix .dSYM,$(TEST_BINS))
	# Objects from before the out-of-tree BUILD_DIR move above. A tree built
	# at an older commit still has these, and nothing else removes them now
	# that .gitignore no longer lists *.o/*.d -- they would show up as
	# untracked and shadow nothing, just confuse.
	rm -f src/*.o src/*.d src/agents/*.o src/agents/*.d

# `make release` builds build/farm-c-release: same sources as `farm-c`, but
# -O2 and *without* ASan/UBSan. The default build above always carries
# sanitizer instrumentation, which inflates CPU time (~2-3x) and memory
# usage (redzones + shadow memory) enough to make either number meaningless
# -- profile CPU time and memory usage against this build, never against
# `farm-c`/`make test`'s binaries. (LeakSanitizer, part of ASan, still works
# fine on the regular build for leak *detection* -- it's peak-memory and
# wall-clock numbers specifically that ASan distorts.)
#
# It is not only profiling that belongs here. Measured on `batch --runs 100
# --seed 42 --workers 8`: 1.61s for `./farm-c` against 0.08s here, for a
# byte-identical CSV. Anything that is actually running the simulator --
# a balance batch, a sweep, a report -- wants this binary, which is why the
# target is named `release` and `profile` is now just an alias for it.
# `make test` still builds and checks both, so the sanitized binary keeps
# gating correctness and this one keeps gating optimizer-induced FP drift.
PROFILE_DIR = $(BUILD_DIR)/profile
OPT = -O2
# Link-time optimization, release/profile builds only. Worth 1.32x sequential
# and 1.24x at 8 workers on `batch --runs 1000 --seed 42` (interleaved
# min-of-5), because this port's hot path is full of one-line accessors that
# live in a different translation unit from every caller and so cannot be
# inlined without it: state.c's plot_columns_get/set and
# planted_crop_columns_get/set (the SoA gather/scatter in weather.c's per-plot
# loop), config.c's config_find_crop/_item (one bounds check each), and the
# allocation latches engine.c reads ~14x per simulated day.
#
# It rides on $(OPT), so it is on the compile *and* link lines. That is not
# incidental: with LTO the real codegen happens at link, so $(FP) must reach
# the link line too or FMA contraction comes back at exactly the step nothing
# else looks at. It does -- every link rule already carries $(FP) -- and
# `make golden-check-profile` is what proves it, which is why `make test`
# runs it. Verified: all 44 baseline combos pass and the full-precision CSV
# is byte-identical to a non-LTO build at both --workers 1 and 8.
LTO = -flto
# LTO writes the real object file to a temp path the linker deletes as soon
# as it is done, so `dsymutil` finds no debug info and `sample`/Instruments
# report the whole binary as `???`. Keeping that object makes the release
# build symbolizable again -- which matters precisely because this is also
# the build you are told to profile. Lands in $(BUILD_DIR), so `clean` takes
# it with everything else.
LTO_OBJ = -Wl,-object_path_lto,$(BUILD_DIR)/farm-c-release-lto.o
PROFILE_LIB_OBJS = $(patsubst src/%.c,$(PROFILE_DIR)/%.o,$(LIB_SRCS))
PROFILE_CJSON_OBJ = $(PROFILE_DIR)/cJSON.o
PROFILE_DEPS = $(PROFILE_LIB_OBJS:.o=.d) $(PROFILE_CJSON_OBJ:.o=.d)

release: $(BUILD_DIR)/farm-c-release

# Kept so existing muscle memory, CI, and CLAUDE.md's profiling guidance
# keep working -- it is the same binary.
profile: release

$(PROFILE_DIR)/%.o: src/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(OPT) $(LTO) $(DBG) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

$(PROFILE_DIR)/cJSON.o: src/cJSON.c include/cJSON.h
	@mkdir -p $(dir $@)
	$(CC) $(CSTD) $(VENDOR_WARN) $(FP) $(THREADS) $(OPT) $(LTO) $(DBG) $(DEPFLAGS) $(INCLUDE) -c $< -o $@

$(BUILD_DIR)/farm-c-release: main.c $(PROFILE_LIB_OBJS) $(PROFILE_CJSON_OBJ)
	$(CC) $(CSTD) $(WARN) $(FP) $(THREADS) $(OPT) $(LTO) $(LTO_OBJ) $(DBG) $(INCLUDE) $^ -o $@ $(LIBS)

-include $(DEPS)
-include $(PROFILE_DEPS)
