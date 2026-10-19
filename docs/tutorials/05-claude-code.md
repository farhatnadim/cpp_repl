# With Claude Code

This repository is also a Claude Code plugin. Its four skills let an agent
run C++ through cpp-repl and set libraries up for it, and you ask for them in
words; its MCP server lets the agent call cpp-repl as a tool. This walk installs the plugin and shows what each skill does, with
replies from real runs (Claude Sonnet 5.5, 2026-10-16), shortened. Those runs
were the plugin's eval cases, whose projects reach cpp-repl through a
launcher, `tools/cpp-repl`.

## Install

<!-- check: off -->
```
$ claude plugin marketplace add farhatnadim/cpp_repl
$ claude plugin install cpp-repl@cpp-repl
```

Inside Claude Code, `/plugin marketplace add farhatnadim/cpp_repl` and
`/plugin install cpp-repl@cpp-repl` do the same. The plugin puts its copy of
cpp-repl's `bin/` on Claude Code's PATH, so nothing else is installed; it
still needs a clang-repl 23 or newer, or Docker. `claude plugin marketplace
update cpp-repl`, then `claude plugin update cpp-repl@cpp-repl`, brings in a
newer one, and `claude plugin details cpp-repl@cpp-repl` lists its skills.

A project can offer it to everyone who opens it in Claude Code, from its
`.claude/settings.json`, as openusd-tutorial does:

```json
{
  "extraKnownMarketplaces": {
    "cpp-repl": { "source": { "source": "github", "repo": "farhatnadim/cpp_repl" } }
  },
  "enabledPlugins": { "cpp-repl@cpp-repl": true }
}
```

## run-cpp: C++ run, not recalled

Ask a question a run can settle:

> What does `std::views::chunk(3)` give for the numbers 1 to 10, and what
> exactly does `std::format("{:>8.3f}", 3.14159)` print, spaces included?
> Check both by running them rather than from memory.

The run-cpp skill fires. The agent pipes the inputs into cpp-repl, since the
prompt waits for a terminal it does not have, and answers from the output:

```
I ran both through `tools/cpp-repl --no-config`. Both calls exited 0.

`std::views::chunk(3)` on 1 to 10 gives four chunks, and the last one is short:

    [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10]]

`std::format("{:>8.3f}", 3.14159)` prints `   3.142`. That is 3 spaces and then
`3.142`, 8 characters in all.
```

Since 2026-10-19 the plugin also declares an MCP server, `cpp-repl --mcp`
([An MCP tool](04-scripts-and-agents.md#an-mcp-tool)), and the agent calls
its `run_cpp` tool in place of the shell command, in one session that stays
for the conversation. The reply reads the same.

A program's variables, after its `main()` ran, need a session that stays,
since `main()`'s `return` ends a batch run. Asked for `median` in a
`src/stats.cpp` without a build, the agent started `cpp-repl --serve`, ran the
file with `--connect`, and sent `std::format("{:.4f}", median)` in a second
`--connect`:

```
After `main()` ran, `median` is 9.9644. I ran it without a build, then read the
variable from the live session. I didn't change `src/stats.cpp`.
```

In a project with its own launcher, the agent follows the project's
AGENTS.md. In openusd-tutorial, asked what `UsdGeomXformCommonAPI::SetPivot`
adds to `xformOpOrder`, it ran `repl/usd-repl` with the inputs piped in:

```
`SetPivot` adds two entries to `xformOpOrder`, one for the pivot and one for its
inverse, and a rotate set afterwards goes between them.

/X.xformOpOrder (token[]) = [xformOp:translate:pivot, !invert!xformOp:translate:pivot]
/X.xformOpOrder (token[]) = [xformOp:translate:pivot, xformOp:rotateXYZ, !invert!xformOp:translate:pivot]
```

The skill says which command fits: the project's launcher, its
`cpp-repl.toml`, a built-in profile, or add-library first for a library
cpp-repl cannot load yet.

## add-library: a library at the prompt

> This app parses its config with yaml-cpp. Set things up so I can load YAML
> snippets and poke at `YAML::Node` values in cpp-repl.

The add-library skill finds the library (its CMake package first, then
pkg-config, then bare headers), resolves its include directories,
definitions and shared libraries, and writes a profile into the project:

```
.cpp-repl/yaml-cpp.toml        the profile: flags, defines, libs, prelude
.cpp-repl/yaml-cpp_repl.h      the prelude: the library's headers and printers
.cpp-repl/yaml-cpp.repl        an example session
.cpp-repl/yaml-cpp.session     the example's transcript
.cpp-repl/yaml-cpp.check.json  the check report
```

Then it checks them:
- the prelude compiles;
- the session starts, and what the profile adds to start-up;
- every input of the example runs;
- the example really calls into the shared libraries;
- a `%save-cpp` of the example builds and prints the same.

It hands back one command:

```
yaml-cpp now works in cpp-repl. Start a session with:

    tools/cpp-repl -c .cpp-repl/yaml-cpp.toml

A bare node prints as block-style YAML, cut after 12 lines. Inside a
std::vector, a node prints as one line of flow-style YAML, such as
[[640, 480], {verbose: true, tags: [a, b]}].

Startup cost: about 0.07 s over a bare session.
```

The profile stays in `.cpp-repl/`, and nothing is written to the plugin.
After a library upgrade, the check runs again against the saved transcript:

<!-- check: off -->
```
$ python3 $PLUGIN/skills/add-library/scripts/check_profile.py --config .cpp-repl/yaml-cpp.toml \
    --example .cpp-repl/yaml-cpp.repl --golden .cpp-repl/yaml-cpp.session
```

Two shorter paths:
- **A library cpp-repl has built in** (Eigen, glm, OpenUSD): the skill writes
  no profile. It hands back `cpp-repl -p glm`, and shows how the types print.
- **A pasted error.** Given a session's `JIT session error: Symbols not
  found: [ _ZN2cv12GaussianBlur... ]`, from a profile that loaded only
  OpenCV's core, the skill names the library that defines the symbol
  (`libopencv_imgproc.so`), adds it to the profile, and checks again.

## add-printer: values that read well

A library's own `operator<<` can print far too much, such as every pixel of
an image. Asked to make a `cv::Mat` print readably, the add-printer skill
writes `repl::Show` and `repl::Put` specialisations into the profile's
prelude, and checks them:

| You type | You get |
| --- | --- |
| `img`, a 240×320 `CV_8UC3` image | `Mat 240×320 8UC3`, then the first and last three rows and columns |
| `small`, a 3×3 `CV_32F` | `Mat 3×3 32FC1`, then the three rows |
| `std::vector<cv::Mat>{img, small}` | `[Mat(240×320 8UC3), [[1, 0, 0], [0, 1, 0], [0, 0, 1]]]` |

A built-in profile's types (`-p usd`) get printers in a header of yours,
layered over the profile with a TOML, since the profile belongs to cpp-repl.

## contribute-profile: a built-in -p NAME

In a clone of this repository, `/cpp-repl:contribute-profile NAME` turns a
profile that add-library made and checked into a built-in `cpp-repl -p NAME`.
It writes the discovery module, the header, a golden session, a test, the
example and the README row, ready for a pull request. The `glm` profile was
made this way. It runs only when asked by name.

## How the skills are checked

The plugin's eval cases in `evals/` run each skill against a prompt in a
fresh workspace, and grade the files it wrote, the tools it used and its
reply. `evals/headless.py` runs them with `claude -p`. On 2026-10-16, all
nine cases passed with Claude Sonnet 5.5, for $1.46 in all:
- yaml-cpp, spdlog, OpenCV and glm;
- the OpenCV printers;
- the pasted error;
- the two run-cpp cases;
- a CMake question where no skill should fire.

## Other agents

Codex reads AGENTS.md, gemini-cli reads it too here (`.gemini/settings.json`),
and Claude Code reads it where a project has no CLAUDE.md. cpp-repl's
AGENTS.md says how an agent runs cpp-repl. The skills themselves are Markdown,
in `skills/*/SKILL.md`, for any agent to read.

[Scripts and agents](04-scripts-and-agents.md) shows the commands themselves.
