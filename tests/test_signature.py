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
