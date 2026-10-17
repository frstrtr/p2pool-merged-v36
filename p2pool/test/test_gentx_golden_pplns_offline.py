# -*- coding: utf-8 -*-
'''
Offline CI gate for golden set #2 (LTC gentx over a populated tracker).

Re-runs p2pool.test.gen_gentx_golden_pplns with emit=False and asserts that
OLD == NEW still holds and that the committed fixtures in
p2pool/test/golden/pplns/ match the regenerated artifact, tracker state and
intermediate PPLNS expectations byte-for-byte.

    ~/.pyenv/versions/2.7.18/bin/python -m unittest \
        p2pool.test.test_gentx_golden_pplns_offline
'''

from __future__ import division

import glob
import json
import os
import unittest

from p2pool.test import gen_gentx_golden_pplns as gen

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden', 'pplns')


class GentxGoldenPplnsOfflineTest(unittest.TestCase):
    def test_old_equals_new_and_matches_fixtures(self):
        committed = glob.glob(os.path.join(GOLDEN_DIR, 'ltc_gentx_pplns_*.json'))
        self.assertTrue(committed, 'no committed PPLNS fixtures found in %s' % GOLDEN_DIR)

        results = dict(gen.run(emit=False, verbose=False))
        self.assertEqual(len(results), len(committed),
                         'fixture count (%d) != generated count (%d)'
                         % (len(committed), len(results)))

        for path in committed:
            basename = os.path.basename(path)
            self.assertIn(basename, results,
                          'committed fixture %s has no generated counterpart' % basename)
            with open(path, 'rb') as f:
                on_disk = json.load(f)
            # round-trip through json so int/float/str types compare like-for-like
            regenerated = json.loads(json.dumps(results[basename]))
            for key in ('artifact', 'tracker_state', 'expected_intermediate'):
                self.assertEqual(
                    on_disk[key], regenerated[key],
                    '%s drift for %s (regenerate with '
                    'p2pool.test.gen_gentx_golden_pplns only if the change is '
                    'intentional and cross-impl reviewed)' % (key, basename))


if __name__ == '__main__':
    unittest.main()
