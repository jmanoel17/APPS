#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2.6 - workload availability probe is independent from Fast Close/accounting."""
import os
import sys
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from dpn.availability import (AVAILABLE, UNAVAILABLE, INCONCLUSIVE,
                              NullAvailabilityProbe, SequenceAvailabilityProbe)


class TestAvailability(unittest.TestCase):
    def test_sequence_and_calls(self):
        p = SequenceAvailabilityProbe([UNAVAILABLE, AVAILABLE])
        r1 = p.probe("G", {"public_ip": "1.1.1.1"})
        r2 = p.probe("G", {"public_ip": "2.2.2.2"})
        self.assertEqual(r1.status, UNAVAILABLE)
        self.assertEqual(r2.status, AVAILABLE)
        self.assertEqual(len(p.calls), 2)

    def test_inconclusive_is_not_available(self):
        p = SequenceAvailabilityProbe([INCONCLUSIVE])
        self.assertEqual(p.probe("G", {"public_ip": "1.1.1.1"}).status, INCONCLUSIVE)

    def test_disabled_probe_is_available(self):
        self.assertEqual(NullAvailabilityProbe().probe("G", {}).status, AVAILABLE)


if __name__ == "__main__":
    unittest.main()
