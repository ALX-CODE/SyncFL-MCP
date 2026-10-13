from pathlib import Path

import pytest

from fl_studio_mcp.utils import asset_catalog as catalog


def asset(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path.resolve()


def test_catalog_classifies_samples_presets_and_plugin_database(tmp_path):
    sample = asset(tmp_path, "Audio/KiCk.WAV")
    preset = asset(tmp_path, "Plugin presets/Synth/Soft.fst")
    generator = asset(tmp_path, "Plugin database/Installed/Generators/VST3/Serum 2.fst")
    effect = asset(tmp_path, "Plugin database/Effects/Filter/Pro-Q 4.FST")
    asset(tmp_path, "Plugin database/Effects/Filter/Pro-Q 4.nfo")
    asset(tmp_path, "Plugins/Fake.dll")

    result = catalog.find_assets("", roots=[tmp_path])

    assert result["scan_complete"] and not result["truncated"]
    assert {entry["path"] for entry in result["assets"]} == {
        str(sample), str(preset), str(generator), str(effect),
    }
    by_name = {entry["name"]: entry for entry in result["assets"]}
    assert by_name["KiCk"]["kind"] == "sample"
    assert by_name["Soft"]["kind"] == "preset"
    assert by_name["Serum 2"]["plugin_kind"] == "generator"
    assert by_name["Pro-Q 4"]["plugin_kind"] == "effect"


def test_kind_filter_and_case_insensitive_name_search(tmp_path):
    sample = asset(tmp_path, "Audio/Kick.WAV")
    asset(tmp_path, "Plugin database/Generators/Kick.fst")
    asset(tmp_path, "Plugin database/Effects/Kick.fst")
    result = catalog.find_assets("kICk", "sample", [tmp_path])
    assert [entry["path"] for entry in result["assets"]] == [str(sample)]
    assert len(catalog.find_assets("kick", "plugin", [tmp_path])["assets"]) == 2
    assert len(catalog.find_assets("kick", "generator", [tmp_path])["assets"]) == 1


def test_serum_native_presets_are_discoverable_without_claiming_loadability(tmp_path):
    preset = asset(tmp_path, "Presets/Soft.SerumPreset")
    result = catalog.find_assets("soft", "preset", [tmp_path])
    assert result["assets"] == [dict(
        name="Soft", path=str(preset), kind="preset", preset_format="serum"
    )]
    assert not catalog.find_assets("soft", "plugin", [tmp_path])["assets"]


def test_limits_keep_exact_match_first_and_report_truncation(tmp_path):
    for index in range(5):
        asset(tmp_path, f"Audio/Kick {index}.wav")
    exact = asset(tmp_path, "Audio/Kick.wav")
    result = catalog.find_assets("kick", roots=[tmp_path], limit=1)
    assert result["assets"][0]["path"] == str(exact)
    assert result["matching_files"] == 6
    assert result["truncated"] and result["scan_complete"]


def test_overlapping_roots_do_not_duplicate_assets(tmp_path):
    asset(tmp_path, "Audio/Kick.wav")
    result = catalog.find_assets("kick", roots=[tmp_path / "Audio", tmp_path, tmp_path])
    assert len(result["assets"]) == result["matching_files"] == 1


def test_resolve_requires_exact_name_and_matching_plugin_kind(tmp_path):
    exact = asset(tmp_path, "Plugin database/Generators/Serum 2.fst")
    asset(tmp_path, "Plugin database/Generators/Serum 2 FX.fst")
    asset(tmp_path, "Plugin database/Effects/Serum 2.fst")
    for name in ["serum 2", "SERUM 2.FST"]:
        assert catalog.resolve_plugin(name, "generator", roots=[tmp_path])["path"] == str(exact)
    with pytest.raises(ValueError, match="No exact"):
        catalog.resolve_plugin("Serum", "generator", roots=[tmp_path])


def test_resolve_rejects_duplicate_exact_names(tmp_path):
    asset(tmp_path, "Plugin database/Generators/Synths/Serum 2.fst")
    asset(tmp_path, "Plugin database/Installed/Generators/VST3/Serum 2.fst")
    with pytest.raises(ValueError, match="Ambiguous"):
        catalog.resolve_plugin("Serum 2", "generator", roots=[tmp_path])


def favorite_roots(tmp_path, monkeypatch):
    user = tmp_path / "user"
    stock = tmp_path / "stock"
    monkeypatch.setattr(catalog, "get_fl_settings_dir", lambda: user / "Settings")
    monkeypatch.setattr(catalog, "_default_roots", lambda: [user / "Presets", stock])
    return user / "Presets" / "Plugin database", stock / "Plugin database"


@pytest.mark.parametrize("kind,folder", [("generator", "Generators"), ("effect", "Effects")])
def test_default_resolution_prefers_user_favorite_over_installed_stock(
    tmp_path, monkeypatch, kind, folder,
):
    user, stock = favorite_roots(tmp_path, monkeypatch)
    favorite = asset(user, f"{folder}/Synth.fst")
    asset(user, f"Installed/{folder}/VST3/Synth.fst")
    asset(stock, f"{folder}/Synth.fst")
    assert catalog.resolve_plugin("Synth", kind)["path"] == str(favorite)
    with pytest.raises(ValueError, match="Ambiguous"):
        catalog.resolve_plugin("Synth", kind, roots=[user, stock])


def test_default_resolution_falls_back_only_without_exact_favorite(tmp_path, monkeypatch):
    user, stock = favorite_roots(tmp_path, monkeypatch)
    asset(user, "Generators/Synth Variation.fst")
    installed = asset(user, "Installed/Generators/VST3/Synth.fst")
    assert catalog.resolve_plugin("Synth", "generator")["path"] == str(installed)


def test_default_resolution_rejects_ambiguous_favorites_without_fallback(tmp_path, monkeypatch):
    user, stock = favorite_roots(tmp_path, monkeypatch)
    asset(user, "Generators/A/Synth.fst")
    asset(user, "Generators/B/Synth.fst")
    asset(stock, "Generators/Synth.fst")
    with pytest.raises(ValueError, match="Ambiguous"):
        catalog.resolve_plugin("Synth", "generator")


def test_resolve_finds_exact_match_past_many_fuzzy_results(tmp_path):
    for index in range(110):
        asset(tmp_path, f"Plugin database/Generators/Serum 2 variation {index}.fst")
    exact = asset(tmp_path, "Plugin database/Generators/Serum 2.fst")
    assert catalog.resolve_plugin("Serum 2", "generator", roots=[tmp_path])["path"] == str(exact)


def test_scan_budget_is_reported_and_resolution_refuses_incomplete_search(tmp_path, monkeypatch):
    asset(tmp_path, "Plugin database/Generators/Serum 2.fst")
    monkeypatch.setattr(catalog, "MAX_ENTRIES", 1)
    result = catalog.find_assets("Serum 2", roots=[tmp_path])
    assert result["truncated"] and not result["scan_complete"]
    assert result["warnings"]
    with pytest.raises(ValueError, match="incomplete"):
        catalog.resolve_plugin("Serum 2", "generator", roots=[tmp_path])


def test_depth_budget(tmp_path, monkeypatch):
    asset(tmp_path, "one/two/Kick.wav")
    monkeypatch.setattr(catalog, "MAX_DEPTH", 1)
    result = catalog.find_assets("kick", roots=[tmp_path])
    assert result["truncated"] and not result["assets"]


def test_time_budget(tmp_path, monkeypatch):
    asset(tmp_path, "Kick.wav")
    monkeypatch.setattr(catalog, "MAX_SECONDS", 0)
    result = catalog.find_assets("kick", roots=[tmp_path])
    assert result["truncated"] and not result["scan_complete"]


def test_default_roots_use_shared_user_data_helper(tmp_path, monkeypatch):
    sample = asset(tmp_path, "Audio/Kick.wav")
    monkeypatch.setattr(catalog, "get_fl_settings_dir", lambda: tmp_path / "Settings")
    roots = catalog._default_roots()
    assert tmp_path / "Audio" in roots
    monkeypatch.setattr(catalog, "_default_roots", lambda: [tmp_path / "Audio"])
    assert catalog.find_assets("kick")["assets"][0]["path"] == str(sample)


def test_default_plugin_search_only_scans_plugin_database(tmp_path, monkeypatch):
    plugin = asset(tmp_path, "Presets/Plugin database/Generators/Serum 2.fst")
    asset(tmp_path, "Presets/Plugin presets/Serum 2.fst")
    asset(tmp_path, "Audio/Serum 2.wav")
    monkeypatch.setattr(
        catalog, "_default_roots", lambda: [tmp_path / "Presets", tmp_path / "Audio"],
    )
    result = catalog.find_assets("Serum 2", kind="generator")
    assert result["assets"][0]["path"] == str(plugin)
    assert result["scanned_files"] == 1
    assert result["roots"] == [str(tmp_path / "Presets" / "Plugin database")]


def test_directory_and_file_symlinks_do_not_escape_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = asset(tmp_path, "outside/Secret.wav")
    try:
        (root / "linked").symlink_to(outside.parent, target_is_directory=True)
        (root / "Leak.wav").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    result = catalog.find_assets("", roots=[root])
    assert not result["assets"]
    assert result["skipped_links"] == 2
    with pytest.raises(ValueError, match="symlink or junction"):
        catalog.find_assets("", roots=[root / "linked"])


@pytest.mark.parametrize("roots", [["relative"], [Path(Path.cwd().anchor)], "C:/"])
def test_rejects_broad_or_invalid_roots(roots):
    with pytest.raises(ValueError):
        catalog.find_assets("", roots=roots)


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
def test_invalid_limit(limit, tmp_path):
    with pytest.raises(ValueError, match="limit"):
        catalog.find_assets("", roots=[tmp_path], limit=limit)


def test_rejects_missing_root_and_invalid_query_kind(tmp_path):
    with pytest.raises(ValueError, match="unavailable"):
        catalog.find_assets("", roots=[tmp_path / "missing"])
    with pytest.raises(ValueError, match="query"):
        catalog.find_assets("bad\0query", roots=[tmp_path])
    with pytest.raises(ValueError, match="kind"):
        catalog.find_assets("", "dll", roots=[tmp_path])
    with pytest.raises(ValueError, match="kind"):
        catalog.resolve_plugin("Serum", "plugin", roots=[tmp_path])
