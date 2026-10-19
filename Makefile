# cpp_repl: tests, Docker images, compat probes and the benchmark.
# `make help` lists the targets.

PYTHON     ?= python3
# The clang-repl cpp-repl itself would run: the newest one, as config.find_clang_repl picks it.
CLANG_REPL ?= $(shell $(PYTHON) -c 'import sys; sys.path.insert(0, "src"); from cpp_repl.config import find_clang_repl; print(find_clang_repl())' 2>/dev/null)
# The release the images carry: config.LLVM_RELEASE, the one place it is set.
LLVM       ?= $(shell $(PYTHON) -c 'import sys; sys.path.insert(0, "src"); from cpp_repl.config import LLVM_RELEASE; print(LLVM_RELEASE)')
LLVM_REF   ?= llvmorg-23.1.3
IMAGE      ?= cpp-repl:llvm$(LLVM)
TUTORIAL   ?= ../openusd-tutorial
BENCH_PYTHON ?= python3
# The probe results make compat-diff compares to what is checked in.
FILE       ?= tests/compat/results/compat-llvm-snapshot.json

# The Dockerfile lives in the package, so that an installed cpp-repl builds the images too.
DOCKER_DIR := src/cpp_repl/docker
DOCKERFILE_SHA := $(shell sha256sum $(DOCKER_DIR)/Dockerfile | cut -d' ' -f1)
DOCKER_BUILD = docker build --build-arg DOCKERFILE_SHA=$(DOCKERFILE_SHA)
# The checkout (read-only) and, when USD_ROOT is set, OpenUSD, mounted at
# their own paths.
DOCKER_RUN = docker run --rm --init -i --user $$(id -u):$$(id -g) -e HOME=/tmp \
	-v $(CURDIR):$(CURDIR):ro -w $(CURDIR) \
	$(if $(USD_ROOT),-e USD_ROOT=$(USD_ROOT) -v $(USD_ROOT):$(USD_ROOT):ro)

.PHONY: help check check-docker wheel-check validate need-clang-repl pr need-issue-branch issue version tag test test-host test-docker test-snapshot parity tutorials tutorials-update \
        llvm compat compat-llvm compat-snapshot compat-docker compat-diff compat-table track compare compare-docker compare-doc \
        docker docker-bench docker-snapshot \
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
# The output of each run is captured, echoed and then searched: `tee /dev/stderr`
# would open /dev/stderr for writing, which truncates a file that captures the
# run (make check > log 2>&1).
wheel-check:  ## build the wheel with uv and run cpp-repl from it
	rm -rf dist build/uv-tool build/uv-bin
	uv build --no-sources
	UV_TOOL_DIR=$(CURDIR)/build/uv-tool UV_TOOL_BIN_DIR=$(CURDIR)/build/uv-bin uv tool install --reinstall dist/cpp_repl-*.whl
	cd build && ./uv-bin/cpp-repl --version
	cd build && out=$$(./uv-bin/cpp-repl --no-config --docker --dry-run) && echo "$$out" && \
	  echo "$$out" | grep -q -- '-m cpp_repl' && ! echo "$$out" | grep -q 'bin/cpp-repl'
	@if [ -n "$(CLANG_REPL)" ]; then \
	  cd build && out=$$(printf 'std::vector<int> v{3, 1, 2};\nstd::ranges::sort(v);\nv\n' \
	    | CPP_REPL_CLANG_REPL=$(CLANG_REPL) ./uv-bin/cpp-repl -q) && echo "$$out" && echo "$$out" | grep -qx '\[1, 2, 3\]'; \
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
# The title and the body reach gh through the environment, as typed: pasted into
# the recipe, their backticks and $ would run as shell, and make would expand
# them before exporting them.
unexport TITLE BODY
issue: export ISSUE_TITLE := $(value TITLE)
issue: export ISSUE_BODY := $(value BODY)
issue:  ## file an issue on the board: make issue KIND=bug|feature|task TITLE="one sentence" [BODY="..."]
	@label=$$(case "$(KIND)" in bug) echo bug;; feature) echo enhancement;; task) echo task;; esac); \
	test -n "$$label" -a -n "$$ISSUE_TITLE" || { echo 'make issue KIND=bug|feature|task TITLE="one sentence that states the fact" [BODY="..."]' >&2; exit 2; }; \
	url=$$(gh issue create --label "$$label" --title "$$ISSUE_TITLE" --body "$$ISSUE_BODY") && echo "$$url" && \
	gh project item-add 18 --owner farhatnadim --url "$$url" > /dev/null

# A release: make version on an issue branch and a pull request, then make tag
# on main once it is merged; .github/workflows/release.yml does the rest.
version: need-issue-branch  ## set the version in src/cpp_repl/__init__.py and the README's wheel, and commit: make version VERSION=x.y.z, then make pr
	@echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo 'make version VERSION=x.y.z' >&2; exit 2; }
	sed -i "s/^__version__ = '.*'$$/__version__ = '$(VERSION)'/" src/cpp_repl/__init__.py
	sed -i -E "s#releases/download/v[0-9.]+/cpp_repl-[0-9.]+-py3-none-any.whl#releases/download/v$(VERSION)/cpp_repl-$(VERSION)-py3-none-any.whl#" README.md
	git commit -m "release: $(VERSION)" src/cpp_repl/__init__.py README.md

tag:  ## on main after the merge: tag it at the version in __init__.py and push the tag, which publishes the release
	@test "$$(git rev-parse --abbrev-ref HEAD)" = main || { echo "make tag: run it on main" >&2; exit 2; }
	@git diff --quiet HEAD || { echo "make tag: the working tree has changes" >&2; exit 2; }
	git fetch -q origin && git merge -q --ff-only origin/main
	@v=$$(sed -n "s/^__version__ = '\(.*\)'$$/\1/p" src/cpp_repl/__init__.py); \
	  ! git rev-parse -q --verify "refs/tags/v$$v" > /dev/null || { echo "make tag: v$$v exists; make version first" >&2; exit 2; }; \
	  git tag -a "v$$v" -m "cpp-repl $$v" && git push origin "v$$v" && echo "v$$v pushed: release.yml publishes it"

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

compat-llvm:  ## probe the clang-repl of the release image: tests/compat/results/compat-llvmN.json
	$(DOCKER_RUN) $(IMAGE) python3 tests/compat/harness.py > tests/compat/results/compat-llvm$(LLVM).json

compat-snapshot:  ## probe the clang-repl of the snapshot image: tests/compat/results/compat-llvm-snapshot.json
	$(DOCKER_RUN) cpp-repl:llvm-snapshot python3 tests/compat/harness.py \
	  > tests/compat/results/compat-llvm-snapshot.json

compat-docker: compat-llvm compat-snapshot  ## probe the clang-repl of the release and snapshot images

compat-diff:  ## a probe run against the checked-in results: make compat-diff [FILE=tests/compat/results/compat-llvm-snapshot.json]
	git show HEAD:$(FILE) | $(PYTHON) scripts/compat_diff.py - $(FILE)

track: docker-snapshot test-snapshot compat-snapshot compat-diff  ## what track.yml runs weekly: the snapshot image, the suite and the probes in it, the probes diffed

compat-table:  ## the README's "Why a front end" table, written between its markers from the probes
	$(PYTHON) scripts/compat_table.py --write README.md tests/compat/results/*.json

llvm:  ## print the release major, config.LLVM_RELEASE
	@echo $(LLVM)

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

# bench/agents: Claude Code with and without cpp-repl, through Harbor (bench/agents/README.md).
AGENTS_MODEL ?= anthropic/claude-sonnet-5-5

need-harbor:
	@command -v harbor > /dev/null || { echo 'bench/agents needs Harbor: uv tool install harbor==0.24.0' >&2; exit 2; }

agents-smoke: need-harbor  ## bench/agents tier 0: oracle and nop on both tasks, then one trial per arm (needs ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)
	$(PYTHON) bench/agents/run.py --tier 0 --model $(AGENTS_MODEL)

agents-bench: need-harbor  ## a tier of bench/agents: make agents-bench TIER=1 [ARM=mcp] [AGENTS_MODEL=...]
	$(PYTHON) bench/agents/run.py --tier $(TIER) $(if $(ARM),--arm $(ARM)) --model $(AGENTS_MODEL)

agents-report:  ## bench/agents' report from its jobs, into bench/agents/results/
	$(PYTHON) bench/agents/report.py --out bench/agents/results/report-$$(date -u +%Y%m%dT%H%M%SZ)
