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
    recipe = _compiled.build_recipe()
    manifest = {
        "manifest_version": _compiled.MANIFEST_VERSION,
        "build_tag": _compiled.build_tag(),
        "build_recipe": recipe,
        "build_recipe_sha256": _compiled.recipe_hash(recipe),
        "modules": {},
    }
    manifest.update(overrides)
    return manifest


def _manifest_with_recipe(**recipe_overrides):
    """A manifest whose recipe is internally consistent but not this tree's."""
    recipe = {**_compiled.build_recipe(), **recipe_overrides}
    return _manifest(build_recipe=recipe, build_recipe_sha256=_compiled.recipe_hash(recipe))


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


def test_manifest_missing_entry_keys_falls_back_instead_of_raising(isolated):
    """A valid JSON file that is not a manifest must take the fallback path.

    This used to raise KeyError straight out of `activate()`, which is not a
    fallback at all -- it crashed the import of `simulation` itself, so the
    one guarantee this module makes (you always get *something* that runs)
    did not hold for a truncated or hand-edited manifest.
    """
    modules, problems = _compiled._verify(_manifest(modules={"simulation.markets": {}}))

    assert modules == {}
    assert sorted(problems) == [
        "simulation.markets: manifest entry is missing 'artifact'",
        "simulation.markets: manifest entry is missing 'source'",
        "simulation.markets: manifest entry is missing 'source_sha256'",
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param("not a dict at all", id="not-an-object"),
        pytest.param({"manifest_version": _compiled.MANIFEST_VERSION}, id="missing-everything"),
    ],
)
def test_structurally_broken_manifests_are_rejected(isolated, manifest):
    modules, problems = _compiled._verify(manifest)
    assert modules == {}
    assert problems


def test_manifest_entry_of_the_wrong_type_is_rejected(isolated):
    modules, problems = _compiled._verify(_manifest(modules={"simulation.markets": ["a", "list"]}))
    assert modules == {}
    assert "is not a JSON object" in problems[0]


def test_bool_does_not_satisfy_an_int_field(isolated):
    """`True == 1` in Python, and bool is an int subclass; neither should let
    a garbage manifest through a version check."""
    modules, problems = _compiled._verify(_manifest(manifest_version=True))
    assert modules == {}
    assert "manifest_version" in problems[0]


def test_module_the_tree_does_not_compile_is_rejected(isolated):
    entry = _entry_for("simulation.markets")
    modules, problems = _compiled._verify(_manifest(modules={"simulation._compiled": entry}))
    assert modules == {}
    assert "not a module this tree compiles" in problems[0]


def test_entry_pointing_at_another_modules_source_is_rejected(isolated):
    """The hash check is only meaningful if the path it covers is the right
    one -- otherwise a manifest can pass by naming a file nobody edited."""
    entry = _entry_for("simulation.markets")
    entry["source"] = "simulation/weather.py"
    entry["source_sha256"] = _compiled.source_hash(_compiled.REPO_ROOT / "simulation/weather.py")
    (isolated / "stub.so").write_bytes(b"")

    modules, problems = _compiled._verify(_manifest(modules={"simulation.markets": entry}))

    assert modules == {}
    assert "manifest names source" in problems[0]


@pytest.mark.parametrize("artifact", ["../escape.so", "/etc/passwd", ""])
def test_artifact_path_escaping_the_build_dir_is_rejected(isolated, artifact):
    entry = _entry_for("simulation.markets", artifact_name=artifact)
    modules, problems = _compiled._verify(_manifest(modules={"simulation.markets": entry}))
    assert modules == {}
    assert "escapes" in problems[0]


def test_build_with_different_directives_is_rejected(isolated):
    """The directives are what make the compiled output bit-exact with the
    reference implementation. Recording them without checking them is worth
    nothing -- an artifact built with annotation_typing on would load happily.
    """
    recipe = _compiled.build_recipe()
    recipe["directives"] = {**recipe["directives"], "annotation_typing": "True"}
    modules, problems = _compiled._verify(
        _manifest(build_recipe=recipe, build_recipe_sha256=_compiled.recipe_hash(recipe))
    )

    assert modules == {}
    assert "different recipe" in problems[0]
    assert "directives" in problems[0]


def test_build_missing_a_required_float_flag_is_rejected(isolated):
    modules, problems = _compiled._verify(_manifest_with_recipe(required_cflags=["-O2"]))
    assert modules == {}
    assert "required_cflags" in problems[0]


def test_build_from_a_different_compiler_is_rejected(isolated):
    modules, problems = _compiled._verify(_manifest_with_recipe(compiler="gcc-14"))
    assert modules == {}
    assert "gcc-14" in problems[0]


def test_build_from_an_older_recipe_version_is_rejected(isolated):
    modules, problems = _compiled._verify(
        _manifest_with_recipe(recipe_version=_compiled.BUILD_RECIPE_VERSION - 1)
    )
    assert modules == {}
    assert "recipe_version" in problems[0]


def test_recipe_hash_must_match_its_own_recipe(isolated):
    """Editing the recipe to match your build does not make the build valid."""
    modules, problems = _compiled._verify(_manifest(build_recipe_sha256="0" * 64))
    assert modules == {}
    assert "does not match its own recorded hash" in problems[0]


def test_matching_recipe_is_accepted(isolated):
    """Guards the tests above: they must fail for the reason stated, not
    because a correct recipe is rejected too."""
    (isolated / "stub.so").write_bytes(b"")
    modules, problems = _compiled._verify(
        _manifest(modules={"simulation.markets": _entry_for("simulation.markets")})
    )
    assert problems == []
    assert set(modules) == {"simulation.markets"}


def test_artifact_dir_honors_the_override(monkeypatch, tmp_path):
    """tools/build_cython.py verifies a staged build through this path before
    publishing it, so the override has to actually redirect the loader."""
    monkeypatch.delenv("FARM_COMPILED_DIR", raising=False)
    assert _compiled.artifact_dir() == _compiled.BUILD_ROOT / _compiled.build_tag()

    monkeypatch.setenv("FARM_COMPILED_DIR", str(tmp_path))
    assert _compiled.artifact_dir() == tmp_path

    monkeypatch.setenv("FARM_COMPILED_DIR", "   ")
    assert _compiled.artifact_dir() == _compiled.BUILD_ROOT / _compiled.build_tag()


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
