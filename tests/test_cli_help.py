"""Tests for the CLI's `--help` output.

The strategy roster in `single`/`replay` help is generated from
AGENT_REGISTRY and each agent's own `description`, so the point of these
tests is that help text cannot drift from what the simulator actually does:
registering an agent documents it, and forgetting a description fails here
rather than silently printing a blank line to users.
"""

import argparse

import pytest

import main
from main import AGENT_REGISTRY, build_parser, strategy_roster


@pytest.fixture(scope="module")
def parser():
    return build_parser()


def _help_for(parser, command=None):
    if command is None:
        return parser.format_help()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices[command].format_help()


# --- the generated roster stays in sync with the registry -----------------


def test_roster_lists_every_registered_strategy():
    roster = strategy_roster()
    for name in AGENT_REGISTRY:
        assert name in roster, f"{name} is registered but missing from --help"


def test_every_registered_agent_has_a_usable_description():
    for name, agent_cls in AGENT_REGISTRY.items():
        description = getattr(agent_cls, "description", "")
        assert description and description.strip(), (
            f"{name} has no description, so its --help entry would be blank"
        )


def test_roster_renders_each_description_in_full():
    """Wrapped continuation lines must not drop any of the description."""
    roster = strategy_roster()
    collapsed = " ".join(roster.split())
    for name, agent_cls in AGENT_REGISTRY.items():
        assert " ".join(agent_cls.description.split()) in collapsed, (
            f"{name}'s description is truncated in the rendered roster"
        )


def test_roster_respects_the_requested_indent():
    for line in strategy_roster(indent="    ").splitlines():
        assert line.startswith("    ")


def test_roster_lines_stay_within_terminal_width():
    for line in strategy_roster().splitlines():
        assert len(line) <= 80, f"roster line exceeds 80 columns: {line!r}"


# --- every option is documented -------------------------------------------


@pytest.mark.parametrize("command", ["single", "replay", "batch"])
def test_every_option_has_help_text(parser, command):
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for option in action.choices[command]._actions:
        assert option.help, f"{command} {option.option_strings} has no help text"


@pytest.mark.parametrize("command", ["single", "replay", "batch"])
def test_each_command_help_shows_examples(parser, command):
    help_text = _help_for(parser, command)
    assert "examples:" in help_text
    assert f"main.py {command}" in help_text


def test_top_level_help_documents_the_parallelism_option(parser):
    """`--workers` is defined on the `batch` subparser, so it does not appear
    on the top-level page at all unless the epilog names it. That gap is what
    makes the only concurrency control in the tool easy to miss.
    """
    help_text = _help_for(parser)

    assert "parallelism:" in help_text
    assert "--workers" in help_text
    # Execution is ProcessPoolExecutor-based; keep the help from drifting into
    # calling it threading, which is what sent a reader looking for --threads.
    assert "process-based" in help_text


def test_top_level_help_shows_examples_for_every_command(parser):
    help_text = _help_for(parser)
    assert "examples:" in help_text
    for command in ("single", "replay", "batch"):
        assert f"main.py {command}" in help_text


@pytest.mark.parametrize("command", ["single", "replay"])
def test_strategy_commands_document_the_roster(parser, command):
    help_text = _help_for(parser, command)
    for name in AGENT_REGISTRY:
        assert name in help_text


def test_batch_help_documents_its_output_artifacts(parser):
    help_text = _help_for(parser, "batch")
    for artifact in ("run_results.csv", "config_snapshot.json", "summary_report.md"):
        assert artifact in help_text


def test_batch_help_states_the_actual_strategy_count(parser):
    assert str(len(AGENT_REGISTRY)) in _help_for(parser, "batch")


def _example_invocations(help_text: str) -> list[list[str]]:
    """Pull runnable `python3 main.py ...` lines out of rendered help.

    Scraped from the real help rather than hand-listed, so editing an
    example's text is what this checks -- a hand-written copy would keep
    passing while the printed command went stale. Lines carrying a
    `<placeholder>` (e.g. the `main.py <command> --help` pointer) are
    documentation rather than invocations, so they are skipped.
    """
    invocations = []
    for raw in help_text.splitlines():
        line = raw.strip().strip("`")
        if not line.startswith("python3 main.py") or "<" in line:
            continue
        invocations.append(line.split()[2:])
    return invocations


@pytest.mark.parametrize("command", [None, "single", "replay", "batch"])
def test_documented_examples_actually_parse(parser, command):
    """Every example printed in help must be a valid invocation."""
    examples = _example_invocations(_help_for(parser, command))
    assert examples, f"no examples found in {command or 'top-level'} help"

    for argv in examples:
        parsed = parser.parse_args(argv)
        assert parsed.func is not None, f"documented example does not run: {argv}"


# --- shortened metavars must not hide the valid choices -------------------


def test_invalid_strategy_error_still_lists_every_choice(parser, capsys):
    """--strategy uses metavar=NAME to keep the usage line short; argparse
    must still enumerate the valid strategies when one is rejected.
    """
    with pytest.raises(SystemExit):
        parser.parse_args(["single", "--strategy", "not_a_strategy"])

    message = capsys.readouterr().err
    for name in AGENT_REGISTRY:
        assert name in message


def test_help_does_not_crash_for_any_subcommand(parser):
    for command in ("single", "replay", "batch"):
        assert _help_for(parser, command)


def test_module_docstring_examples_match_the_cli(parser):
    """main.py's own docstring examples must stay runnable too."""
    for line in main.__doc__.splitlines():
        line = line.strip()
        if not line.startswith("python main.py"):
            continue
        parser.parse_args(line.split()[2:])
