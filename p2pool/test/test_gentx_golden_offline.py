# -*- coding: utf-8 -*-
'''
Offline CI gate for the LTC gentx path (G1).

Loads the committed golden-vector fixtures in p2pool/test/golden/ and
re-asserts, with NO network / NO daemon / NO WorkerBridge, that:

  1. the OLD monolithic path (Share.generate_transaction) and the NEW split
     path (generate_transaction_template + finalize_generate_transaction)
     still produce BYTE-IDENTICAL gentx / share_info / other_transaction_hashes
     (this is the consensus-critical DOA-fix identity), and

  2. the regenerated artifact matches the committed fixture byte-for-byte
     (guards against silent drift in the gentx serialization -- the fixtures
     are the frozen cross-impl target for c2pool's ltc_gentx_dump).

Any byte difference here would fork the sharechain against v35/v36 peers or
invalidate the c2pool reference target, so this runs on every CI push.

    ~/.pyenv/versions/2.7.18/bin/python -m unittest \
        p2pool.test.test_gentx_golden_offline
'''

from __future__ import division

import glob
import json
import os
import unittest

from p2pool.test import gen_gentx_golden as gen

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), 'golden')


class GentxGoldenOfflineTest(unittest.TestCase):
    def test_old_equals_new_and_matches_fixtures(self):
        committed = glob.glob(os.path.join(GOLDEN_DIR, 'ltc_gentx_*.json'))
        self.assertTrue(committed, 'no committed golden fixtures found in %s' % GOLDEN_DIR)

        # run() re-runs BOTH paths under frozen determinism and internally
        # raises if OLD != NEW for any scenario/user.  emit=False -> no writes.
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
            regenerated = results[basename]
            # Compare the artifact (the consensus bytes + c2pool contract).
            self.assertEqual(
                on_disk['artifact'], regenerated['artifact'],
                'artifact drift for %s: committed fixture no longer matches the '
                'gentx path output (regenerate with p2pool.test.gen_gentx_golden '
                'only if the change is intentional and cross-impl reviewed)' % basename)
            # A committed gentx must actually carry bytes.
            self.assertTrue(on_disk['artifact']['gentx_hex'],
                            'empty gentx_hex in %s' % basename)


if __name__ == '__main__':
    unittest.main()
