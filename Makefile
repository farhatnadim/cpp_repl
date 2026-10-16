# cpp_repl: tests, Docker images, compat probes and the benchmark.
# `make help` lists the targets.

PYTHON     ?= python3
# The clang-repl cpp-repl itself would run: the newest one, as config.find_clang_repl picks it.
CLANG_REPL ?= $(shell $(PYTHON) -c 'import sys; sys.path.insert(0, "src"); from cpp_repl.config import find_clang_repl; print(find_clang_repl())' 2>/dev/null)
LLVM       ?= 23
LLVM_REF   ?= llvmorg-23.1.3
IMAGE      ?= cpp-repl:llvm$(LLVM)
TUTORIAL   ?= ../openusd-tutorial
BENCH_PYTHON ?= python3

DOCKERFILE_SHA := $(shell sha256sum docker/Dockerfile | cut -d' ' -f1)
DOCKER_BUILD = docker build --build-arg DOCKERFILE_SHA=$(DOCKERFILE_SHA)
# The checkout (read-only) and, when USD_ROOT is set, OpenUSD, mounted at
# their own paths.
DOCKER_RUN = docker run --rm --init -i --user $$(id -u):$$(id -g) -e HOME=/tmp \
	-v $(CURDIR):$(CURDIR):ro -w $(CURDIR) \
	$(if $(USD_ROOT),-e USD_ROOT=$(USD_ROOT) -v $(USD_ROOT):$(USD_ROOT):ro)

.PHONY: help test test-host test-docker test-snapshot parity compat compat-docker compat-table \
        compare compare-docker compare-doc docker docker-bench docker-snapshot docker-src bench bench-quick bench-host

help:  ## this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | expand -t 18

test:  ## unit and fake-backend tests: no clang-repl needed
	$(PYTHON) -m pytest -q -m 'not backend'

test-host:  ## every test, against the host's clang-repl (CLANG_REPL=...)
	CPP_REPL_CLANG_REPL=$(CLANG_REPL) $(PYTHON) -m pytest -q -rs

test-docker:  ## every test, inside cpp-repl:llvm23
	$(DOCKER_RUN) $(IMAGE) python3 -m pytest -q -p no:cacheprovider -rs

test-snapshot:  ## every test, inside cpp-repl:llvm-snapshot (LLVM main)
	$(DOCKER_RUN) cpp-repl:llvm-snapshot python3 -m pytest -q -p no:cacheprovider -rs

parity:  ## openusd-tutorial's lessons through cpp-repl -p usd and compiled, diffed
	$(PYTHON) scripts/parity.py --tutorial $(TUTORIAL)

compat:  ## probe the host's clang-repl
	$(PYTHON) tests/compat/harness.py --clang-repl $(CLANG_REPL) \
	  > tests/compat/results/compat-$$($(CLANG_REPL) --version | sed -n 's/.*LLVM version \([0-9]*\).*/\1/p').json

compat-docker:  ## probe the clang-repl of the llvm23 and snapshot images
	$(DOCKER_RUN) $(IMAGE) python3 tests/compat/harness.py > tests/compat/results/compat-llvm$(LLVM).json
	$(DOCKER_RUN) cpp-repl:llvm-snapshot python3 tests/compat/harness.py \
	  > tests/compat/results/compat-llvm-snapshot.json

compat-table:  ## the README's "Why a front end" table, from the probes
	$(PYTHON) scripts/compat_table.py tests/compat/results/*.json

compare:  ## raw clang-repl against cpp-repl, the host's clang-repl: compare/results/compare-N.json
	$(PYTHON) compare/compare.py run \
	  > compare/results/compare-$$($(CLANG_REPL) --version | sed -n 's/.*LLVM version \([0-9]*\).*/\1/p').json

compare-docker:  ## the same inside the llvm23 and snapshot images
	$(DOCKER_RUN) $(IMAGE) python3 compare/compare.py run > compare/results/compare-llvm$(LLVM).json
	$(DOCKER_RUN) cpp-repl:llvm-snapshot python3 compare/compare.py run > compare/results/compare-llvm-snapshot.json

compare-doc:  ## the tables of compare/README.md, from every run
	$(PYTHON) compare/compare.py render compare/results/compare-*.json

docker:  ## build cpp-repl:llvm23: clang-repl from apt.llvm.org (LLVM=23)
	$(DOCKER_BUILD) --target apt --build-arg LLVM_MAJOR=$(LLVM) -t $(IMAGE) docker/
	docker tag $(IMAGE) cpp-repl:llvm$$(docker run --rm $(IMAGE) clang-repl --version \
	  | sed -n 's/.*LLVM version \([0-9.]*\).*/\1/p')

docker-bench: docker  ## build cpp-repl:bench-llvm23: adds libomp, g++, NumPy, Eigen 5
	$(DOCKER_BUILD) --target bench --build-arg LLVM_MAJOR=$(LLVM) -t cpp-repl:bench-llvm$(LLVM) docker/

docker-snapshot:  ## build cpp-repl:llvm-snapshot: LLVM main, from apt.llvm.org
	$(DOCKER_BUILD) --target snapshot -t cpp-repl:llvm-snapshot docker/

docker-src:  ## build clang-repl from source at LLVM_REF (30 to 60 minutes)
	$(DOCKER_BUILD) --target src --build-arg LLVM_REF=$(LLVM_REF) -t cpp-repl:llvm-src-$(LLVM_REF) docker/

bench-quick:  ## a 3-minute benchmark run in the bench image
	bench/docker.sh --quick

bench:  ## the full benchmark protocol in the bench image (about 30 minutes)
	bench/docker.sh

bench-host:  ## the benchmark on the host (BENCH_PYTHON: a Python whose NumPy uses OpenBLAS)
	$(PYTHON) bench/run.py --python $(BENCH_PYTHON)
