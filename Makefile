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

# The Dockerfile lives in the package, so that an installed cpp-repl builds the images too.
DOCKER_DIR := src/cpp_repl/docker
DOCKERFILE_SHA := $(shell sha256sum $(DOCKER_DIR)/Dockerfile | cut -d' ' -f1)
DOCKER_BUILD = docker build --build-arg DOCKERFILE_SHA=$(DOCKERFILE_SHA)
# The checkout (read-only) and, when USD_ROOT is set, OpenUSD, mounted at
# their own paths.
DOCKER_RUN = docker run --rm --init -i --user $$(id -u):$$(id -g) -e HOME=/tmp \
	-v $(CURDIR):$(CURDIR):ro -w $(CURDIR) \
	$(if $(USD_ROOT),-e USD_ROOT=$(USD_ROOT) -v $(USD_ROOT):$(USD_ROOT):ro)

.PHONY: help check check-docker wheel-check validate need-clang-repl pr need-issue-branch issue test test-host test-docker test-snapshot parity tutorials tutorials-update \
        compat compat-docker compat-table compare compare-docker compare-doc docker docker-bench docker-snapshot \
        docker-src bench bench-quick bench-host

help:  ## this list
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | expand -t 18

check: need-clang-repl test-host wheel-check validate  ## before a pull request: every test against the host's clang-repl (23 or newer), the wheel, then the plugin's manifests

check-docker: docker test-docker validate  ## what CI runs: the same inside cpp-repl:llvm23, built first, where clang compiles against GCC 13

validate:  ## the plugin's manifests and skills (claude plugin validate)
	claude plugin validate .

# The wheel as a user installs it: built with uv, installed with uv tool under
# build/, run from outside the checkout. The version and the docker command
# need neither Docker nor clang-repl; the session runs when the host has one.
wheel-check:  ## build the wheel with uv and run cpp-repl from it
	rm -rf dist build/uv-tool build/uv-bin
	uv build --no-sources
	UV_TOOL_DIR=$(CURDIR)/build/uv-tool UV_TOOL_BIN_DIR=$(CURDIR)/build/uv-bin uv tool install --reinstall dist/cpp_repl-*.whl
	cd build && ./uv-bin/cpp-repl --version
	cd build && ./uv-bin/cpp-repl --no-config --docker --dry-run | tee /dev/stderr | grep -q -- '-m cpp_repl'
	cd build && ! ./uv-bin/cpp-repl --no-config --docker --dry-run | grep -q 'bin/cpp-repl'
	@if [ -n "$(CLANG_REPL)" ]; then \
	  cd build && printf 'std::vector<int> v{3, 1, 2};\nstd::ranges::sort(v);\nv\n' \
	    | CPP_REPL_CLANG_REPL=$(CLANG_REPL) ./uv-bin/cpp-repl -q | tee /dev/stderr | grep -qx '\[1, 2, 3\]'; \
	else echo "wheel-check: no clang-repl on the host: the session is not run"; fi

# Without a clang-repl the backend tests skip, and a passing make check would say nothing.
need-clang-repl:
	@test -n "$(CLANG_REPL)" || { echo "make check: no clang-repl 23 or newer found; install one, or pass CLANG_REPL=/path/to/clang-repl" >&2; exit 2; }

pr: need-issue-branch check  ## make check, push this branch (N-slug, from gh issue develop N --checkout) and open its pull request, which closes issue N
	@n=$$(git rev-parse --abbrev-ref HEAD | sed -n 's/^\([0-9][0-9]*\)-.*/\1/p'); \
	git push -u origin HEAD && \
	gh pr create --assignee @me --title "$$(gh issue view $$n --json title --jq .title)" \
	  --body "$$(printf 'Closes #%s\n\n## What changed\n\n\n\n## Checks\n\n- [x] `make check` passed locally\n- [ ] docs and tutorial transcripts updated where behaviour changed\n' "$$n")"

# The pull request closes the issue the branch is named after, so the name comes first.
need-issue-branch:
	@git rev-parse --abbrev-ref HEAD | grep -q '^[0-9][0-9]*-' || \
	  { echo "make pr: the branch is not named N-slug; start from: gh issue develop N --checkout" >&2; exit 2; }

# gh 2.45's `gh issue create --project` fails (it still asks for classic projects), hence item-add.
issue:  ## file an issue on the board: make issue KIND=bug|feature|task TITLE="one sentence" [BODY="..."]
	@label=$$(case "$(KIND)" in bug) echo bug;; feature) echo enhancement;; task) echo task;; esac); \
	test -n "$$label" -a -n "$(TITLE)" || { echo 'make issue KIND=bug|feature|task TITLE="one sentence that states the fact" [BODY="..."]' >&2; exit 2; }; \
	url=$$(gh issue create --label "$$label" --title "$(TITLE)" --body "$(BODY)") && echo "$$url" && \
	gh project item-add 18 --owner farhatnadim --url "$$url" > /dev/null

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

tutorials:  ## docs/tutorials' transcripts against runs of the host's clang-repl
	$(PYTHON) scripts/check_tutorial.py --clang-repl $(CLANG_REPL) docs/tutorials/*.md

tutorials-update:  ## rewrite the tutorials' transcripts that differ from a run (read the diff)
	$(PYTHON) scripts/check_tutorial.py --clang-repl $(CLANG_REPL) --update docs/tutorials/*.md

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
	$(DOCKER_BUILD) --target apt --build-arg LLVM_MAJOR=$(LLVM) -t $(IMAGE) $(DOCKER_DIR)/
	docker tag $(IMAGE) cpp-repl:llvm$$(docker run --rm $(IMAGE) clang-repl --version \
	  | sed -n 's/.*LLVM version \([0-9.]*\).*/\1/p')

docker-bench: docker  ## build cpp-repl:bench-llvm23: adds libomp, g++, NumPy, Eigen 5
	$(DOCKER_BUILD) --target bench --build-arg LLVM_MAJOR=$(LLVM) -t cpp-repl:bench-llvm$(LLVM) $(DOCKER_DIR)/

docker-snapshot:  ## build cpp-repl:llvm-snapshot: LLVM main, from apt.llvm.org
	$(DOCKER_BUILD) --target snapshot -t cpp-repl:llvm-snapshot $(DOCKER_DIR)/

docker-src:  ## build clang-repl from source at LLVM_REF (30 to 60 minutes)
	$(DOCKER_BUILD) --target src --build-arg LLVM_REF=$(LLVM_REF) -t cpp-repl:llvm-src-$(LLVM_REF) $(DOCKER_DIR)/

bench-quick:  ## a 3-minute benchmark run in the bench image
	bench/docker.sh --quick

bench:  ## the full benchmark protocol in the bench image (about 30 minutes)
	bench/docker.sh

bench-host:  ## the benchmark on the host (BENCH_PYTHON: a Python whose NumPy uses OpenBLAS)
	$(PYTHON) bench/run.py --python $(BENCH_PYTHON)
