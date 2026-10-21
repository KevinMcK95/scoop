import os
import socket
import unittest
from unittest import mock

from scoop import gaia_status

LISTING = """<jobs>
  <jobref id="1"><phase>EXECUTING</phase><ownerId>ANONYMOUS</ownerId><creationTime>2026-10-21T13:21:28Z</creationTime></jobref>
  <jobref id="2"><phase>QUEUED</phase><ownerId>ANONYMOUS</ownerId><creationTime>2026-10-21T13:22:00Z</creationTime></jobref>
  <jobref id="3"><phase>COMPLETED</phase><ownerId>ANONYMOUS</ownerId><creationTime>2026-10-21T13:00:00Z</creationTime></jobref>
  <jobref id="4"><phase>QUEUED</phase><ownerId>ANONYMOUS</ownerId><creationTime>2026-10-21T13:23:00Z</creationTime></jobref>
</jobs>"""


def job(name, query="SELECT COUNT(*) AS n FROM gaiadr3.gaia_source"):
    jobname = f'<parameter id="jobname">{name}</parameter>' if name else ""
    return f'<job><parameters>{jobname}<parameter id="query">{query}</parameter></parameters></job>'


DEAD_PID = 2 ** 22 + 12345  # beyond macOS and Linux pid ranges in practice


class GaiaStatusTests(unittest.TestCase):
    def fake_get(self, url, timeout_s):
        host = socket.gethostname()
        return {"/async": LISTING,
                "/async/1": job(f"scoop:{host}:{os.getpid()}"),   # this live process: not stale
                "/async/2": job(f"scoop:{host}:{DEAD_PID}"),      # a stopped SCOOP run: stale
                "/async/4": job(None)}[url[len(gaia_status.SERVERS["heidelberg"]):]]

    def test_queue_marks_only_dead_scoop_jobs_on_this_host_stale(self):
        with mock.patch.object(gaia_status, "_get", self.fake_get):
            jobs = gaia_status.queue("heidelberg")
        self.assertEqual([(j["id"], j["phase"], j["stale"]) for j in jobs],
                         [("1", "EXECUTING", False), ("2", "QUEUED", True), ("4", "QUEUED", False)])

    def test_clean_up_aborts_stale_jobs_only_and_diagnoses(self):
        ok = {"ok": True, "seconds": 0.4, "error": None}
        aborted = []
        with mock.patch.object(gaia_status, "_get", self.fake_get), \
                mock.patch.object(gaia_status, "probe", return_value=ok), \
                mock.patch.object(gaia_status, "abort", side_effect=lambda s, i: aborted.append(i) or True):
            st = gaia_status.status(clean_up=True)
        self.assertEqual(aborted, ["2"])
        self.assertEqual(st["queues"]["heidelberg"]["queued"], 1)
        text = " ".join(st["diagnosis"])
        self.assertIn("Aborted 1 stale SCOOP job", text)
        self.assertIn("tell the user rather than aborting", text)

    def test_no_server_answering_points_at_the_network(self):
        down = {"ok": False, "seconds": 20.0, "error": "URLError: no route to host"}
        with mock.patch.object(gaia_status, "probe", return_value=down):
            st = gaia_status.status()
        self.assertEqual(st["queues"], {})
        self.assertIn("network", st["diagnosis"][0])

    def test_clear_queue_blames_the_query(self):
        ok = {"ok": True, "seconds": 0.4, "error": None}
        with mock.patch.object(gaia_status, "probe", return_value=ok), \
                mock.patch.object(gaia_status, "_get", return_value="<jobs></jobs>"):
            st = gaia_status.status()
        self.assertIn("Galactic plane", st["diagnosis"][0])


if __name__ == "__main__":
    unittest.main()
