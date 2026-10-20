import unittest

from scoop.resolve import parse_sesame

M31 = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Target option="SNV">
  <name>M31</name>
  <Resolver name="S=Simbad (CDS, via client/server), oid=1575544">
    <otype>AG?</otype>
    <jpos>00:42:44.33 +41:16:07.5</jpos>
    <jradeg>10.684708333</jradeg>
    <jdedeg>41.268750000</jdedeg>
    <oname>M  31</oname>
  </Resolver>
</Target>
</Sesame>"""

NOTHING = """<?xml version="1.0"?><Sesame><Target option="SNV"><name>NotARealStar123</name>
<Resolver name="S=Simbad"><INFO>*** Nothing found ***</INFO></Resolver></Target></Sesame>"""


class SesameTests(unittest.TestCase):
    def test_first_resolver_with_a_position(self):
        self.assertEqual(parse_sesame(M31), {"ra": 10.684708333, "dec": 41.26875, "name": "M  31",
                                             "otype": "AG?", "resolver": "Simbad"})

    def test_nothing_found_or_garbage(self):
        self.assertIsNone(parse_sesame(NOTHING))
        self.assertIsNone(parse_sesame("<html>503</html"))


if __name__ == "__main__":
    unittest.main()
