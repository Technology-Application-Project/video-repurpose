import unittest
from pipeline.intro_ending_review import review_intro_ending, compare_opening_options

EVIDENCE = {'c1': 'Educational investment helps students prepare for future skills and jobs.'}

def storyboard(title='How Can Education Prepare Future Workers?', subtitle='Future skills and jobs', takeaway='Educational investment helps prepare students for future jobs.', cta='Full lecture in description.', question='What skills matter next?', seconds=6, ids=None):
    ids = ['c1'] if ids is None else ids
    return {'opening': {'title': title, 'subtitle': subtitle, 'evidence_ids': ids},
            'ending': {'takeaway': takeaway, 'question': question, 'cta': cta,
                       'evidence_ids': ids, 'seconds': seconds}}

class IntroEndingReviewTests(unittest.TestCase):
    def test_valid_question_hook(self):
        r = review_intro_ending(storyboard(), 'General public', EVIDENCE)
        self.assertEqual(r['result'], 'precheck_pass')
        self.assertTrue(r['human_review_required'])

    def test_missing_question_for_public(self):
        r = review_intro_ending(storyboard(title='Education Prepares Students for Future Skills'), 'General public', EVIDENCE)
        self.assertIn('question_hook_required', r['fields'][0]['warnings'])

    def test_researcher_can_use_statement(self):
        r = review_intro_ending(storyboard(title='Education Prepares Students for Future Skills'), 'Researcher', EVIDENCE)
        self.assertNotIn('question_hook_required', r['fields'][0]['warnings'])

    def test_throat_clearing(self):
        r = review_intro_ending(storyboard(title='In this video we discuss education'), 'Researcher', EVIDENCE)
        self.assertIn('generic_throat_clearing', r['fields'][0]['warnings'])

    def test_title_over_limit(self):
        r = review_intro_ending(storyboard(title='How Do Many Education Investments Help Future Students Find Better Jobs?'), 'Kids', EVIDENCE)
        self.assertIn('word_limit_exceeded', r['fields'][0]['warnings'])

    def test_invalid_evidence_id(self):
        r = review_intro_ending(storyboard(ids=['wrong']), 'Kids', EVIDENCE)
        self.assertIn('missing_or_unknown_evidence_id', r['fields'][0]['warnings'])

    def test_weak_overlap(self):
        r = review_intro_ending(storyboard(takeaway='Bananas and elephants enjoy swimming in blue oceans.'), 'Researcher', EVIDENCE)
        self.assertIn('weak_lexical_evidence_match', r['fields'][2]['warnings'])

    def test_fixed_cta(self):
        r = review_intro_ending(storyboard(cta='Click here'), 'Researcher', EVIDENCE)
        self.assertIn('fixed_cta_required_without_source_url', r['fields'][4]['warnings'])

    def test_generic_cta_with_url(self):
        r = review_intro_ending(storyboard(cta='Learn more'), 'Researcher', EVIDENCE, has_source_url=True)
        self.assertIn('generic_cta', r['fields'][4]['warnings'])

    def test_dense_narration(self):
        r = review_intro_ending(storyboard(takeaway='Educational investment helps students prepare for future careers with many different skills and opportunities available to them.', seconds=4), 'Researcher', EVIDENCE)
        self.assertIn('ending_narration_too_dense', r['timing_warnings'])

    def test_too_short_duration(self):
        r = review_intro_ending(storyboard(seconds=2), 'Kids', EVIDENCE)
        self.assertIn('ending_duration_outside_4_to_6_seconds', r['timing_warnings'])

    def test_comparison_prefers_lower_warnings(self):
        options = [storyboard(title='In this video we discuss education'), storyboard()]
        r = compare_opening_options(options, 'General public', EVIDENCE)
        self.assertEqual(r['ranking'][0], 2)

    def test_empty_options(self):
        self.assertEqual(compare_opening_options([], 'Kids', EVIDENCE)['ranking'], [])

    def test_invalid_audience(self):
        with self.assertRaises(ValueError):
            review_intro_ending(storyboard(), 'Unknown', EVIDENCE)

if __name__ == '__main__':
    unittest.main()
