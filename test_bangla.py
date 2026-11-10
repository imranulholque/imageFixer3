"""Checks of banglatext.py (pure Python):  python test_bangla.py"""
import banglatext as bt

fails = 0
def eq(got, want, what=""):
    global fails
    if isinstance(want, str) and bt.has_bangla(want):
        want = bt._normalize(want)        # য় ড় ঢ় can be typed as one letter or as letter + nukta: compare the one-letter form
    if got != want:
        fails += 1
        print("FAIL", what, "\n   got ", got, "\n   want", want)

# ---- phonetic (Avro style)
for roman, want in (("ami", "আমি"), ("bangla", "বাংলা"), ("kolom", "কলম"), ("tumi", "তুমি"), ("tOmake", "তোমাকে"),
                    ("bhalObasi", "ভালোবাসি"), ("prem", "প্রেম"), ("shikkha", "শিক্ষা"), ("swadhIn", "স্বাধীন"),
                    ("bidyaloy", "বিদ্যালয়"), ("rriShi", "ঋষি"), ("Bangla", "বাংলা"), ("2026", "২০২৬"),
                    ("ami tomake", "আমি তমাকে"), ("jNGan", "জ্ঞান"), ("dhonyobad", "ধন্যবাদ"), ("kk", "ক্ক"),
                    ("bangladesh", "বাংলাদেশ"), ("amar sonar bangla.", "আমার সনার বাংলা।"), ("t``", "ৎ"),
                    ("shOmoy", "শোময়"), ("pa^k", "পাঁক")):
    eq(bt.phonetic_text(roman), want, "phonetic " + roman)

# ---- Bijoy -> Unicode, known pairs from public test sets
eq(bt.bijoy_to_unicode("Avwg evsjvq Mvb MvB|"), "আমি বাংলায় গান গাই।", "b2u sentence 1")
long_b = ("‡Kv‡bv †Kv‡bv w`b, AvKvk‡Rvov †gN K‡i, Kv‡R gb e‡m bv| †W‡¯‹i mvg‡bi Rvbvjvi c`©v Zz‡j w`B| "
          "w¯Œ‡b ZvKvB, †`wL bv, `‚ieZ©x AvKvk I Bgvi‡Z wKQz †LvuR _v‡K| Ggb me w`‡bi bvg †KvgjMvÜvi|")
long_u = ("কোনো কোনো দিন, আকাশজোড়া মেঘ করে, কাজে মন বসে না। ডেস্কের সামনের জানালার পর্দা তুলে দিই। "
          "স্ক্রিনে তাকাই, দেখি না, দূরবর্তী আকাশ ও ইমারতে কিছু খোঁজ থাকে। এমন সব দিনের নাম কোমলগান্ধার।")
eq(bt.bijoy_to_unicode(long_b), long_u, "b2u long")

# ---- Unicode -> Bijoy -> Unicode must come back
for u in ("আমি বাংলায় গান গাই।", long_u, "কৃষক", "ব্যবহার", "প্রাণ", "মাত্র", "সূত্র", "ব্রাহ্মণ", "গঞ্জ", "লক্ষ্মী",
          "কর্ম", "দুঃখ", "পড়ে", "যায়", "সংখ্যা ১২৩", "বিশ্ববিদ্যালয়", "কিংবা", "মৌসুমী", "ঢাকা"):
    b = bt.unicode_to_bijoy(u)
    eq(bt.bijoy_to_unicode(b), bt._normalize(u), "round trip " + u + "  via " + b)

# a few direct Unicode -> Bijoy checks (shapes known from the public vectors)
eq(bt.unicode_to_bijoy("আমি বাংলায় গান গাই।"), "Avwg evsjvq Mvb MvB|", "u2b sentence 1")
eq(bt.unicode_to_bijoy("কোনো"), "\u2021Kv\u2021bv", "u2b kono")
eq(bt.unicode_to_bijoy("পর্দা"), "c`\u00a9v", "u2b porda")
eq(bt.unicode_to_bijoy("English 12 text"), "English 12 text", "ascii stays")
eq(bt.unicode_to_bijoy("Avwg"), "Avwg", "ascii (already bijoy) stays under u2b")

# ---- font names
for name, want in (("SutonnyMJ", True), ("SutonnyOMJ", True), ("BornomalaMJ", True), ("Nirmala UI", False),
                   ("Kalpurush", False), ("Arial", False), ("Kalpurush ANSI", True)):
    eq(bt.is_bijoy_font(name), want, "font " + name)

print("OK" if not fails else "%d FAILED" % fails)
raise SystemExit(1 if fails else 0)
