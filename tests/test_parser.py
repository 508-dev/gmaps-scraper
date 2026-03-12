from __future__ import annotations

import json
import unittest

from google_saved_lists.parser import parse_saved_list_artifacts

_LIST_URL = (
    "https://www.google.com/maps/@35.6501307,139.6868459,15z/"
    "data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3"
)
_LIST_NODE = [
    ["UGEPbA20Qd-OH4uoWjmDgQ", 1, None, 1, 1],
    4,
    "https://www.google.com/maps/placelists/list/UGEPbA20Qd-OH4uoWjmDgQ",
    "Owner",
    "Tokyo Dinners",
    "Best spots in the city",
    None,
    None,
    [
        [
            None,
            [
                None,
                None,
                "",
                None,
                "Shibuya, Tokyo",
                [None, None, 35.6501307, 139.6868459],
                ["7451636382641713350", "aux"],
                "/g/11yakumo",
            ],
            "Yakumo",
        ],
        [
            None,
            [
                None,
                None,
                "",
                None,
                "Chuo City, Tokyo",
                [None, None, 35.6915776, 139.7836109],
                ["1234567890123456789"],
                "/g/11sushi",
            ],
            "Sushi Place",
        ],
    ],
]


class ParserTests(unittest.TestCase):
    def test_parses_runtime_state_with_list_id(self) -> None:
        runtime_state = ["noise", _LIST_NODE]

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(parsed.list_id, "UGEPbA20Qd-OH4uoWjmDgQ")
        self.assertEqual(parsed.title, "Tokyo Dinners")
        self.assertEqual(parsed.description, "Best spots in the city")
        self.assertEqual(len(parsed.places), 2)
        self.assertEqual(parsed.places[0].name, "Yakumo")
        self.assertEqual(parsed.places[0].cid, "7451636382641713350")
        self.assertEqual(parsed.places[0].maps_url, "https://maps.google.com/?cid=7451636382641713350")

    def test_falls_back_to_placelist_marker_without_list_id(self) -> None:
        runtime_state = ["noise", _LIST_NODE]

        parsed = parse_saved_list_artifacts(
            "https://www.google.com/maps",
            runtime_state=runtime_state,
        )

        self.assertEqual(parsed.list_id, "UGEPbA20Qd-OH4uoWjmDgQ")
        self.assertEqual(parsed.title, "Tokyo Dinners")
        self.assertEqual(parsed.places[1].name, "Sushi Place")

    def test_decodes_embedded_xssi_blob(self) -> None:
        blob = ")]}'\\n" + json.dumps(_LIST_NODE)

        parsed = parse_saved_list_artifacts(_LIST_URL, script_texts=[blob])

        self.assertEqual(parsed.title, "Tokyo Dinners")
        self.assertEqual(len(parsed.places), 2)


if __name__ == "__main__":
    unittest.main()
