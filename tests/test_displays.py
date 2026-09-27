import unittest
from pathlib import Path

from phonelab.displays import join, parse_dumpsys_display, parse_surfaceflinger

FIX = Path(__file__).parent / "fixtures"
SF = (FIX / "surfaceflinger_display_id_fold_live.txt").read_text()
DD = (FIX / "dumpsys_display_fold_live_excerpt.txt").read_text()


class DisplayParsing(unittest.TestCase):
    def test_surfaceflinger_entries(self):
        sf = parse_surfaceflinger(SF)
        self.assertEqual([e["sf_id"] for e in sf],
                         ["4619827677550801152", "4619827677550801153", "11529215050245041331", "11529215047354549223"])
        self.assertEqual(sf[0]["unique_id"], "local:4619827677550801152")
        self.assertEqual(sf[3]["unique_id"], "virtual:com.android.shell,2000,Cua agent,89")
        self.assertEqual(sf[3]["name"], "Cua agent")

    def test_dumpsys_display_fields(self):
        dd = parse_dumpsys_display(DD)
        inner, outer = dd["local:4619827677550801152"], dd["local:4619827677550801153"]
        self.assertEqual((inner["logical_id"], inner["width"], inner["height"], inner["state"], inner["status_bar_px"]),
                         (0, 2076, 2152, "ON", 160))
        self.assertEqual((outer["logical_id"], outer["state"], outer["status_bar_px"]), (3, "OFF", 159))
        cua = dd["virtual:com.android.shell,2000,Cua agent,89"]
        self.assertEqual((cua["logical_id"], cua["width"], cua["height"], cua["owner"]), (98, 1080, 1920, "com.android.shell"))
        self.assertEqual(dd["virtual:com.android.shell,2000,studio.screen.sharing:0,88"]["logical_id"], 97)

    def test_logical_block_ignores_later_mDisplayId_lines(self):
        noisy = DD + "\n  mDisplayId=0\n  mBaseDisplayInfo=DisplayInfo{\"Bogus\", displayId 0, uniqueId \"local:999\"}\n" * 5
        self.assertNotIn("local:999", parse_dumpsys_display(noisy))

    def test_join_roles_and_order(self):
        displays = join(parse_surfaceflinger(SF), parse_dumpsys_display(DD))
        self.assertEqual([(d.name, d.logical_id, d.role) for d in displays],
                         [("Inner Display", 0, "human"), ("Outer Display", 3, "human"),
                          ("Cua agent", 98, "agent"), ("studio.screen.sharing:0", 97, "ignored")])
        cua = displays[2]
        self.assertEqual((cua.sf_id, cua.kind, cua.status_bar_px), ("11529215047354549223", "virtual", 0))
        self.assertEqual(displays[0].status_bar_px, 160)

    def test_join_unmatched_entry(self):
        sf = parse_surfaceflinger(SF) + [{"sf_id": "42", "kind": "virtual", "name": "Cua agent", "unique_id": "virtual:x,1,Cua agent,1"}]
        extra = [d for d in join(sf, parse_dumpsys_display(DD)) if d.sf_id == "42"][0]
        self.assertIsNone(extra.logical_id)
        self.assertEqual(extra.role, "agent")


if __name__ == "__main__":
    unittest.main()
