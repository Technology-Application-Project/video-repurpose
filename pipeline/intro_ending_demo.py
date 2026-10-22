"""Offline example: python -m pipeline.intro_ending_demo (no cloud calls)."""
import json
from pipeline.intro_ending_review import compare_opening_options


def example(title: str) -> dict:
    return {
        'opening': {'title': title, 'subtitle': 'Future skills and jobs', 'evidence_ids': ['lecture-01']},
        'ending': {'takeaway': 'Educational investment helps students prepare for future jobs.',
                   'question': 'What skills matter next?', 'cta': 'Full lecture in description.',
                   'seconds': 6, 'evidence_ids': ['lecture-01']},
    }


if __name__ == '__main__':
    evidence = {'lecture-01': 'Educational investment helps students prepare for future skills and jobs.'}
    comparison = compare_opening_options(
        [example('In this video we discuss education'),
         example('How Can Education Prepare Future Workers?')],
        'General public', evidence,
    )
    print(json.dumps(comparison, indent=2))
