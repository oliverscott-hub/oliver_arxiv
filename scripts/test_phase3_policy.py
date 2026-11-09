import unittest

from phase3_policy import allowed_choices, grammar_for, parse_response


class Phase3PolicyTests(unittest.TestCase):
    def test_q1_only_rejects_u_in_q1_but_preserves_abstention_elsewhere(self):
        self.assertEqual(allowed_choices("q1_no_u"), ("ABCD", "ABCDU", "ABCDU", "ABCDU", "ABCDU", "ABCDU"))
        with self.assertRaises(ValueError):
            parse_response("Q1=U\nQ2=C\nQ3=C\nQ4=C\nQ5=C\nQ6=C", "q1_no_u")
        choices, score = parse_response("Q1=B\nQ2=U\nQ3=C\nQ4=C\nQ5=C\nQ6=C", "q1_no_u")
        self.assertEqual(choices[1], "U")
        self.assertIsNone(score)

    def test_all_no_u_rejects_u_everywhere_and_scores(self):
        with self.assertRaises(ValueError):
            parse_response("Q1=B\nQ2=U\nQ3=C\nQ4=C\nQ5=C\nQ6=C", "all_no_u")
        self.assertEqual(parse_response("Q1=B\nQ2=C\nQ3=B\nQ4=C\nQ5=C\nQ6=D", "all_no_u")[1], 600)

    def test_grammar_matches_allowed_choices(self):
        for policy in ("standard", "q1_no_u", "all_no_u"):
            grammar = grammar_for(policy)
            for index, choices in enumerate(allowed_choices(policy), 1):
                self.assertIn(f"choice{index} ::= [{choices}]", grammar)


if __name__ == "__main__":
    unittest.main()
