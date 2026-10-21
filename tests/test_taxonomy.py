import pytest

from photo_sorter import taxonomy
from photo_sorter.config import load_config
from photo_sorter.taxonomy import SEP, Taxonomy, dump, leaves, parse

LEGACY = """# Album categories

Legend: ✅ confirmed · ⚠️ guessed

- 📁 Photos - Coral
  - 📁 Outplants
    - ✅ APAL: Acropora palmata (elkhorn coral)
  - ✅ Bleaching
- ✅ Photos - Fragging
- Videos - Coral  ❓ sub-albums unknown
- KML (ignored: Map files)

## Still needed

- the other names
"""


def test_parse_legacy_and_roundtrip_simple():
    roots, tail = parse(LEGACY)
    assert [r.name for r in roots] == ["Photos - Coral", "Photos - Fragging", "Videos - Coral"]
    assert roots[0].children[0].children[0].description == "Acropora palmata (elkhorn coral)"
    assert tail.startswith("## Still needed")
    photo, video = leaves(roots)
    assert set(photo) == {"Photos - Coral › Outplants › APAL", "Photos - Coral › Bleaching", "Photos - Fragging"}
    assert video == {"Videos - Coral": "Videos - Coral"}
    text = dump(roots, tail)
    assert "✅" not in text and "- Photos - Coral\n  - Outplants\n    - APAL: Acropora palmata (elkhorn coral)" in text
    assert text.rstrip().endswith("- the other names")
    roots2, tail2 = parse(text)
    assert leaves(roots2) == (photo, video) and tail2 == tail


def test_crud(tmp_path):
    p = tmp_path / "CATEGORIES.md"
    t = Taxonomy(p)                         # file does not exist yet -> empty
    assert t.roots == [] and t.keys() == []
    assert t.add(None, "Coral", "hard corals") == "Coral"
    assert t.add("Coral", "Bleaching", "pale coral") == "Coral › Bleaching"
    t.add(None, "Sponge")
    with pytest.raises(ValueError):
        t.add(None, "coral")                # duplicate, case-insensitive
    with pytest.raises(ValueError):
        t.add(None, "Bad: name")
    assert t.update("Sponge", name="Sponges", description="all sponges") == "Sponges"
    t.save()
    t2 = Taxonomy(p)
    assert t2.keys() == ["Coral", "Coral › Bleaching", "Sponges"]
    photo, _ = leaves(t2.roots)
    assert photo == {"Coral › Bleaching": "pale coral", "Sponges": "all sponges"}   # Coral is a folder now
    assert t2.delete("Coral") == 2
    t2.save()
    assert Taxonomy(p).keys() == ["Sponges"]
    # the config loader sees the same thing and remembers the file path
    (tmp_path / "c.json").write_text('{"categories_file": "CATEGORIES.md", "source": {"kind": "local", "root": "/tmp"}}', encoding="utf-8")
    cfg = load_config(tmp_path / "c.json")
    assert cfg.categories == {"Sponges": "all sponges"} and cfg.categories_path == p


def test_empty_file_is_allowed(tmp_path):
    (tmp_path / "CATEGORIES.md").write_text("# Categories\n\nnothing yet\n", encoding="utf-8")
    (tmp_path / "c.json").write_text('{"categories_file": "CATEGORIES.md", "source": {"kind": "local", "root": "/tmp"}}', encoding="utf-8")
    cfg = load_config(tmp_path / "c.json")
    assert cfg.categories == {} and cfg.all_categories == {"Review"}


def test_indent_outdent(tmp_path):
    t = Taxonomy(tmp_path / "CATEGORIES.md")
    for name in ("Coral", "Bleaching", "Sponge"):
        t.add(None, name)
    with pytest.raises(ValueError):
        t.indent("Coral")                         # nothing above it
    assert t.indent("Bleaching") == "Coral › Bleaching"
    assert t.keys() == ["Coral", "Coral › Bleaching", "Sponge"]
    with pytest.raises(ValueError):
        t.outdent("Sponge")                       # already top level
    assert t.outdent("Coral › Bleaching") == "Bleaching"
    assert t.keys() == ["Coral", "Bleaching", "Sponge"]   # lands right after its old parent
    t.add("Coral", "Sponge")                       # same name under Coral
    with pytest.raises(ValueError):
        t.outdent("Coral › Sponge")               # would collide with top-level Sponge
