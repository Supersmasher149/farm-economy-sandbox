"""The compiled-build shim must fail toward the reference implementation.

simulation/_compiled.py redirects imports to prebuilt Cython artifacts when
FARM_COMPILED is set. Its value is entirely in what it refuses: an artifact
built from a source file that has since been edited is worse than no artifact
at all, because the resulting "my change did nothing" is invisible.

These tests run with or without a build present -- the verification logic is
exercised against synthesized manifests, so they do not require Cython.
"""

import json
import sys

import pytest

from simulation import _compiled


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Point the shim at an empty artifact directory and let it re-activate.

    sys.meta_path is snapshotted so a test that successfully installs a finder
    cannot leak it into the rest of the session.
    """
    import sys

    original = list(sys.meta_path)
    monkeypatch.setattr(_compiled, "_activated", False)
    monkeypatch.setattr(_compiled, "artifact_dir", lambda: tmp_path)
    yield tmp_path
    sys.meta_path[:] = original


def _manifest(**overrides):
    manifest = {
        "manifest_version": _compiled.MANIFEST_VERSION,
        "build_tag": _compiled.build_tag(),
        "modules": {},
    }
    manifest.update(overrides)
    return manifest


def _entry_for(fullname, artifact_name="stub.so"):
    source = _compiled.compilable_sources()[fullname]
    return {
        "source": str(source.relative_to(_compiled.REPO_ROOT)),
        "source_sha256": _compiled.source_hash(source),
        "artifact": artifact_name,
    }


def test_edited_source_is_rejected_by_name(isolated):
    """The failure this whole mechanism exists to prevent."""
    entry = _entry_for("simulation.markets")
    entry["source_sha256"] = "0" * 64  # as if markets.py had been edited
    (isolated / "stub.so").write_bytes(b"")
    modules, problems = _compiled._verify(_manifest(modules={"simulation.markets": entry}))

    assert modules == {}
    assert len(problems) == 1
    assert "simulation/markets.py" in problems[0]
    assert "changed since it was compiled" in problems[0]


def test_matching_source_is_accepted(isolated):
    (isolated / "stub.so").write_bytes(b"")
    modules, problems = _compiled._verify(
        _manifest(modules={"simulation.markets": _entry_for("simulation.markets")})
    )
    assert problems == []
    assert set(modules) == {"simulation.markets"}


def test_missing_artifact_is_rejected(isolated):
    modules, problems = _compiled._verify(
        _manifest(modules={"simulation.markets": _entry_for("simulation.markets")})
    )
    assert modules == {}
    assert "artifact missing" in problems[0]


def test_manifest_from_another_abi_is_rejected(isolated):
    modules, problems = _compiled._verify(_manifest(build_tag="cpython-999-solaris"))
    assert modules == {}
    assert "cpython-999-solaris" in problems[0]


def test_manifest_from_an_older_builder_is_rejected(isolated):
    modules, problems = _compiled._verify(
        _manifest(manifest_version=_compiled.MANIFEST_VERSION - 1)
    )
    assert modules == {}
    assert "manifest_version" in problems[0]


def test_unset_env_var_does_nothing(isolated, monkeypatch):
    monkeypatch.delenv("FARM_COMPILED", raising=False)
    assert _compiled.activate() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_falsey_env_values_do_nothing(isolated, monkeypatch, value):
    monkeypatch.setenv("FARM_COMPILED", value)
    assert _compiled.activate() is False


def test_missing_manifest_falls_back_with_a_warning(isolated, monkeypatch, capsys):
    monkeypatch.setenv("FARM_COMPILED", "1")
    assert _compiled.activate() is False
    assert "falling back to pure Python" in capsys.readouterr().err


def test_missing_manifest_is_fatal_under_strict(isolated, monkeypatch):
    monkeypatch.setenv("FARM_COMPILED", "strict")
    with pytest.raises(RuntimeError, match="compiled build is unusable"):
        _compiled.activate()


def test_tampered_manifest_falls_back_rather_than_loading(isolated, monkeypatch, capsys):
    """End to end: a real manifest on disk with one bad hash."""
    entry = _entry_for("simulation.markets")
    entry["source_sha256"] = "0" * 64
    (isolated / "stub.so").write_bytes(b"")
    (isolated / "manifest.json").write_text(
        json.dumps(_manifest(modules={"simulation.markets": entry}))
    )
    monkeypatch.setenv("FARM_COMPILED", "1")

    # Counted rather than asserted absent: the session itself may legitimately
    # be running under FARM_COMPILED, in which case a finder is already
    # installed. What matters is that this tampered manifest adds none.
    before = sum(isinstance(f, _compiled._CompiledFinder) for f in sys.meta_path)
    assert _compiled.activate() is False
    assert "changed since it was compiled" in capsys.readouterr().err
    assert sum(isinstance(f, _compiled._CompiledFinder) for f in sys.meta_path) == before


def test_no_in_tree_artifacts_are_shadowing_sources():
    """A compiled module beside its own .py wins on import, silently.

    This is not hypothetical -- building in place is a one-liner and it
    defeats the manifest check entirely, because the finder can decline to
    serve a stale artifact only for the import system to find an even staler
    one sitting in the package directory.
    """
    shadowing = _compiled.in_tree_artifacts()
    assert not shadowing, (
        "compiled modules are shadowing their sources: "
        f"{[str(p.name) for p in shadowing]}. Remove them with "
        "`rm -f simulation/*.so agents/*.so && python3 tools/build_fastplot.py`; "
        "use `python3 tools/build_cython.py` + FARM_COMPILED=1 instead."
    )


def test_fastplot_is_not_mistaken_for_a_shadowing_artifact():
    """_fastplot has no .py counterpart, so it must never be flagged."""
    assert not any("_fastplot" in path.name for path in _compiled.in_tree_artifacts())


def test_never_compiled_modules_are_excluded():
    """_compiled.py must stay importable as source, or nothing works."""
    sources = _compiled.compilable_sources()
    assert "simulation._compiled" not in sources
    assert "simulation.__init__" not in sources
    assert "agents.__init__" not in sources
    # Sanity: the set is not accidentally empty.
    assert "simulation.markets" in sources
    assert "agents.profit_optimizer" in sources
