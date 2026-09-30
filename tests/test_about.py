from rimbisect.about import parse_about


def write(tmp_path, xml, folder="1234567890", published=None):
    about = tmp_path / folder / "About"
    about.mkdir(parents=True)
    (about / "About.xml").write_text(xml, encoding="utf-8")
    if published:
        (about / "PublishedFileId.txt").write_text(published)
    return about / "About.xml"


def test_basic_fields_and_lowercased_ids(tmp_path):
    path = write(tmp_path, """<ModMetaData>
      <name>Some Mod</name><packageId>Author.SomeMod</packageId>
      <supportedVersions><li>1.5</li><li>1.6</li></supportedVersions>
      <modDependencies><li><packageId>brrainz.Harmony</packageId><displayName>Harmony</displayName></li></modDependencies>
      <loadAfter><li>Ludeon.RimWorld</li></loadAfter>
      <forceLoadBefore><li>Other.Mod</li></forceLoadBefore>
    </ModMetaData>""")
    mod = parse_about(path, "1.6")
    assert mod.package_id == "author.somemod"
    assert mod.name == "Some Mod"
    assert [d.package_id for d in mod.dependencies] == ["brrainz.harmony"]
    assert mod.dependencies[0].name == "Harmony"
    assert mod.load_after == ["ludeon.rimworld"]
    assert mod.load_before == ["other.mod"]
    assert mod.supported_versions == ["1.5", "1.6"]
    assert mod.workshop_id == "1234567890"


def test_versioned_lists_replace_the_base_list(tmp_path):
    path = write(tmp_path, """<ModMetaData><packageId>a.b</packageId>
      <modDependencies><li><packageId>old.dep</packageId></li></modDependencies>
      <modDependenciesByVersion><v1.6><li><packageId>new.dep</packageId></li></v1.6></modDependenciesByVersion>
      <loadAfter><li>x.y</li></loadAfter>
      <loadAfterByVersion><v1.5><li>only.for.fifteen</li></v1.5></loadAfterByVersion>
    </ModMetaData>""")
    mod = parse_about(path, "1.6")
    assert [d.package_id for d in mod.dependencies] == ["new.dep"]
    assert mod.load_after == ["x.y"]
    assert [d.package_id for d in parse_about(path, "1.5").dependencies] == ["old.dep"]


def test_alternative_package_ids(tmp_path):
    path = write(tmp_path, """<ModMetaData><packageId>a.b</packageId><modDependencies><li>
      <packageId>dep.one</packageId><alternativePackageIds><li>Dep.Two</li></alternativePackageIds>
    </li></modDependencies></ModMetaData>""")
    dep = parse_about(path, "1.6").dependencies[0]
    assert dep.package_id == "dep.one"
    assert dep.alternatives == ("dep.two",)


def test_xml_the_game_cannot_read_gets_the_id_the_game_makes_up(tmp_path):
    path = write(tmp_path, "<ModMetaData><name>Guns & Roses</name><packageId>g.r</packageId></ModMetaData>",
                 folder="Guns Mod")
    mod = parse_about(path, "1.6")
    assert (mod.package_id, mod.name) == ("anonymous189.gunshmod", "Guns Mod")
    assert "could not be read" in mod.problem


def test_missing_package_id_is_made_up_like_the_game_does(tmp_path):
    path = write(tmp_path, "<ModMetaData><name>Old Mod</name><author>Bob</author>"
                           "<description>Adds things.</description></ModMetaData>")
    mod = parse_about(path, "1.6")
    assert (mod.package_id, mod.name) == ("bob104.oldhmod", "Old Mod")
    assert "no packageId" in mod.problem


def test_a_folder_without_about_xml_is_still_a_mod_to_the_game(tmp_path):
    (tmp_path / "Loose" / "About").mkdir(parents=True)
    assert parse_about(tmp_path / "Loose" / "About" / "About.xml", "1.6").package_id == "anonymous189.loose"


def test_text_is_decoded_like_the_game_does(tmp_path):
    path = write(tmp_path, "")
    path.write_bytes('<?xml version="1.0" encoding="utf-8"?><ModMetaData><name>Café</name>'
                     '<packageId>bob.cafe</packageId></ModMetaData>'.encode("cp1252"))
    assert parse_about(path, "1.6").package_id == "bob.cafe"
    path.write_bytes('<?xml version="1.0" encoding="utf-16"?><ModMetaData><packageId>bob.wide</packageId>'
                     '</ModMetaData>'.encode("utf-16"))
    assert parse_about(path, "1.6").package_id == "bob.wide"
    path.write_bytes(b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-16"?><ModMetaData><packageId>bob.bom</packageId>'
                     b'</ModMetaData>')
    assert parse_about(path, "1.6").package_id == "bob.bom"


def test_tags_in_the_wrong_case_still_count(tmp_path):
    path = write(tmp_path, "<ModMetaData><PackageId>Bob.Case</PackageId><ModDependencies><li>"
                           "<packageID>bob.lib</packageID></li></ModDependencies></ModMetaData>")
    mod = parse_about(path, "1.6")
    assert mod.package_id == "bob.case" and mod.problem is None
    assert [d.package_id for d in mod.dependencies] == ["bob.lib"]


def test_published_file_id_wins_over_folder_name(tmp_path):
    path = write(tmp_path, "<ModMetaData><packageId>a.b</packageId></ModMetaData>", folder="My Mod", published="987654321\n")
    assert parse_about(path, "1.6").workshop_id == "987654321"


def test_local_folder_without_id(tmp_path):
    path = write(tmp_path, "<ModMetaData><packageId>a.b</packageId></ModMetaData>", folder="Mein Mod ünï")
    assert parse_about(path, "1.6").workshop_id is None
