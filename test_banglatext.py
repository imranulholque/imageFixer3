"""Tests for the Bangla / English script split (pure Python, no Qt):  python test_banglatext.py"""
import banglatext as b

T = "Hello \u0986\u09ae\u09bf, \u09a4\u09c1\u09ae\u09bf 123 world\u0964"

# pieces: English stays English, Bangla stays Bangla, spaces / commas go with the text before them
assert b.split_scripts(T) == [("lat", "Hello "), ("bn", "\u0986\u09ae\u09bf, \u09a4\u09c1\u09ae\u09bf "), ("lat", "123 world"), ("bn", "\u0964")]
assert "".join(p for _, p in b.split_scripts(T)) == T

# Bijoy OFF: nothing is converted, every piece gets its own font
txt, runs = b.layout_runs(T, False, "Nirmala UI", "Arial")
assert txt == T and runs[0] == (0, 6, "Arial") and runs[1][2] == "Nirmala UI" and runs[-1][1] == len(T)

# Bijoy ON: only the Bangla pieces are converted; "Hello" / "world" / digits are NOT changed
txt, runs = b.layout_runs(T, True, "SutonnyMJ", "Arial")
assert txt.startswith("Hello Avwg, ") and txt.endswith("123 world|"), txt
assert all(f == "Arial" for s, e, f in runs if txt[s:e].startswith(("Hello", "123")))
assert runs[-1][1] == len(txt)

# Bijoy ON + no Bangla letter at all = typed with a Bijoy keyboard: left as it is, drawn with the Bijoy font
assert b.layout_runs("Avwg evsjvq", True, "SutonnyMJ", "Arial") == ("Avwg evsjvq", [])

# positions are UTF-16 units (Qt): an emoji counts 2
txt, runs = b.layout_runs("hi \U0001F600 \u0986\u09ae\u09bf", False, "B", "A")
assert runs == [(0, 6, "A"), (6, 9, "B")], runs

# edge cases
assert b.layout_runs(" ", False, "B", "A") == (" ", [(0, 1, "A")])
assert b.layout_runs("", False, "B", "A") == ("", [])
assert b.layout_runs("a\nb\u0986", False, "B", "A")[1][-1][1] == 4
print("banglatext: all ok")
