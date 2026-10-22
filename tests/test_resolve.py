import unittest

from scoop.archives.resolve import parse_sesame

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


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_simbad_answers_when_sesame_is_unreachable(self):
        from scoop.archives.resolve import sesame_resolver

        class Response:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                pass

        class Client:
            queries = []

            async def get(self, url, timeout):
                raise ConnectionError("cds.unistra.fr unreachable")

            async def post(self, url, data, timeout):
                self.queries.append(data["QUERY"])
                return Response('main_id,ra,dec,otype\n"NGC   300",13.7227,-37.6842,"GiG"\n')

        client = Client()
        found = await sesame_resolver(client)("O'Neil  NGC 300")
        self.assertEqual((found["ra"], found["name"]), (13.7227, "NGC 300"))
        self.assertIn("Sesame unreachable", found["resolver"])
        self.assertIn("i.id = 'O''Neil NGC 300'", client.queries[0])  # quotes doubled, spaces collapsed


if __name__ == "__main__":
    unittest.main()
