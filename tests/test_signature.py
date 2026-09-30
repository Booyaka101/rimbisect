import re

from rimbisect.signature import Criterion, group_errors, normalize, signature_of


def test_normalize_strips_the_parts_that_vary():
    line = ("Exception ticking Pawn_123 (at 0x1F3A) in 'Some Map' id 6f1c2a3b-0c1d-4e5f-8a9b-0c1d2e3f4a5b "
            "hash deadbeef01 after 2.5 s")
    assert normalize(line) == "Exception ticking Pawn_<N> (at <HEX>) in <Q> id <GUID> hash <HEX> after <N> s"


def test_signature_uses_first_line_and_first_frame():
    a = "NullReferenceException: x\n  at Foo.Bar (int) [0x00012]\n  at Baz.Qux ()"
    b = "NullReferenceException: x\n  at Foo.Bar (int) [0x00034]\n  at Other.Caller ()"
    c = "NullReferenceException: x\n  at Something.Else ()"
    assert signature_of(a) == signature_of(b) != signature_of(c)


def test_a_harmony_patch_does_not_change_the_signature():
    head = "Exception ticking Pawn: System.NullReferenceException: Object reference not set\n"
    plain = head + "  at Verse.Pawn.Tick () [0x0001c] in <c5b2f7a8>:0\n  at Verse.TickList.Tick ()"
    wrapped = head + "  at (wrapper dynamic-method) Verse.Pawn.Verse.Pawn.Tick_Patch1(Verse.Pawn)\n  at X.Y ()"
    monomod = head + "  at (wrapper dynamic-method) MonoMod.Utils.DynamicMethodDefinition.Verse.Pawn.Tick_Patch3(Verse.Pawn)"
    dmd = head + "  at Verse.Pawn.DMD<DMD<Tick_Patch2>?-1201524736::Tick_Patch2> (Verse.Pawn )"
    other = head + "  at Verse.Pawn.TickRare () [0x0001c] in <c5b2f7a8>:0"
    assert signature_of(plain) == signature_of(wrapped) == signature_of(monomod) == signature_of(dmd)
    assert signature_of(plain) != signature_of(other)
    assert signature_of(plain).endswith("| Pawn.Tick")


def test_methods_of_generic_types_stay_apart():
    head = "System.NullReferenceException: Object reference not set\n"
    add = signature_of(head + "  at Verse.ThingOwner`1[T].TryAdd (T item) [0x00012] in <abc>:0")
    remove = signature_of(head + "  at Verse.ThingOwner`1[T].Remove (T item) [0x00012] in <abc>:0")
    assert add.endswith("| ThingOwner`<N>.TryAdd") and remove.endswith("| ThingOwner`<N>.Remove")


def test_apostrophes_are_not_quotes():
    assert normalize("Can't find Pawn's bed 'Bed_12'") == "Can't find Pawn's bed <Q>"


def test_grouping_counts_repeats_most_frequent_first():
    groups = group_errors([("Could not find def 12", 1), ("Missing texture 'a'", 1),
                           ("Could not find def 99", 3), ("", 1)])
    assert [g.count for g in groups] == [4, 1]
    assert groups[0].headline == "Could not find def 12"


def test_criteria():
    sig = Criterion(signature=signature_of("Could not find def 12"))
    assert sig.error_matches("Could not find def 40") and not sig.error_matches("Could not load")
    assert not sig.line_matches("Could not find def 40")
    regex = Criterion(pattern=re.compile("NoSuchField"))
    assert regex.error_matches("<NoSuchField> is not a field") and regex.line_matches("x NoSuchField")
    assert Criterion(slower_than=60).describe() == {"kind": "slower_than", "value": 60}
    assert Criterion().describe()["kind"] == "crash"
