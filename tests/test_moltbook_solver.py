"""Offline checks for the Moltbook verification-challenge solver.

The cases are real challenges from moltbook_challenges.log; the expected answers are the
plain arithmetic of each one. Case 5 mixes units ("23 cm per second ... 15 newtons ...
combined") and the intended answer, 38, is a judgment call.
"""
import moltbook

CASES = [
    ("A] LoOoBbSsStTeR' S^ ClAaW] ExXeRrTs- ThIrTy TwO ] NoOoToNs+ AnNd[ ThE OtThEr- ClAaW ExXeRrTs/ FoOuR ] NoOoToNs, ToTaL/ FoR^cE?", "36"),
    ("A] Lo.OoB.sT eR] ClAw- FoRcE Is TwEnTy ThReE NoOoToNs ^, Um AnD It MuLlTiIpLiIeS /By FiVe ]~ HoW MuCh ToTaL FoRcE {< lxq>?", "115"),
    ("A] lO^bSt-Er'S ClAw] ExErTs- TwEnTy] ThReE^ NeWtOnS, aNd[ tHe- OtHeR ClAw] AdDs/ FiVe~ NeWtOnS; HoW> MuCh^ ToTaL/ FoRcE?", "28"),
    ("A] LoO.bSt-Er S^wI mS wItH vElAwCiiTyY Of TwEnTy FiVee MeTeRs/ PeR SeCoNd ~ um AnD DiViDeD| By FiVee, WhAt Is ThE nEw SpEeD?", "5"),
]


def test_logged_challenges():
    for text, expected in CASES:
        assert moltbook.solve_challenge(text) == expected, text


def test_total_or_combined_means_add_when_nothing_else_is_given():
    assert moltbook.solve_challenge("a claw exerts thirty newtons and the other claw exerts twelve newtons how many total") == "42"
    assert moltbook.solve_challenge("a lobster swims at twenty three centimeters per second and its claw exerts fifteen newtons how much combined") == "38"


def test_explicit_operation_beats_total():
    assert moltbook.solve_challenge("force is twenty three newtons and it multiplies by five how much total") == "115"


def test_refuses_to_guess():
    assert moltbook.solve_challenge("the lobster is happy today and has twelve friends") is None
    assert moltbook.solve_challenge("how much force") is None
