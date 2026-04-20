from __future__ import annotations

import copy
import json
import unittest

from gmaps_scraper.parser import ParseError, parse_saved_list_artifacts

_LIST_URL = (
    "https://www.google.com/maps/@35.6501307,139.6868459,15z/"
    "data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3"
)
_SHORT_URL = "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18"
_REDIRECT_URL = (
    "https://www.google.com/maps/@30.5370705,125.4120472,6z/"
    "data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3?entry=ttu"
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
            "Delicious wonton ramen. You can ask for a mix of white and dark broth.",
            None,
            None,
            None,
            [[[[3, None, "104356373423434804635", "❤️", [1776133481, 81561000]]]]],
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
        self.assertEqual(
            parsed.places[0].note,
            "Delicious wonton ramen. You can ask for a mix of white and dark broth.",
        )
        self.assertTrue(parsed.places[0].is_favorite)
        self.assertFalse(parsed.places[1].is_favorite)
        self.assertEqual(parsed.places[0].cid, "7451636382641713350")
        self.assertEqual(
            parsed.places[0].maps_url,
            "https://www.google.com/maps/search/?api=1&query=Yakumo%2C+Shibuya%2C+Tokyo",
        )

    def test_falls_back_to_placelist_marker_without_list_id(self) -> None:
        runtime_state = ["noise", _LIST_NODE]

        parsed = parse_saved_list_artifacts(
            "https://www.google.com/maps",
            runtime_state=runtime_state,
        )

        self.assertEqual(parsed.list_id, "UGEPbA20Qd-OH4uoWjmDgQ")
        self.assertEqual(parsed.title, "Tokyo Dinners")
        self.assertEqual(parsed.places[1].name, "Sushi Place")
        self.assertFalse(parsed.places[1].is_favorite)

    def test_prefers_list_id_from_resolved_redirect_url(self) -> None:
        runtime_state = ["noise", _LIST_NODE]

        parsed = parse_saved_list_artifacts(
            _SHORT_URL,
            resolved_url=_REDIRECT_URL,
            runtime_state=runtime_state,
        )

        self.assertEqual(parsed.source_url, _SHORT_URL)
        self.assertEqual(parsed.resolved_url, _REDIRECT_URL)
        self.assertEqual(parsed.list_id, "UGEPbA20Qd-OH4uoWjmDgQ")
        self.assertEqual(parsed.title, "Tokyo Dinners")

    def test_decodes_embedded_xssi_blob(self) -> None:
        blob = ")]}'\\n" + json.dumps(_LIST_NODE)

        parsed = parse_saved_list_artifacts(_LIST_URL, script_texts=[blob])

        self.assertEqual(parsed.resolved_url, None)
        self.assertEqual(parsed.title, "Tokyo Dinners")
        self.assertEqual(len(parsed.places), 2)

    def test_decodes_app_initialization_state_assignment(self) -> None:
        blob = f"window.APP_INITIALIZATION_STATE={json.dumps(_LIST_NODE)};"

        parsed = parse_saved_list_artifacts(_LIST_URL, script_texts=[blob])

        self.assertEqual(parsed.list_id, "UGEPbA20Qd-OH4uoWjmDgQ")
        self.assertEqual(parsed.title, "Tokyo Dinners")

    def test_builds_search_query_url_when_cid_is_missing(self) -> None:
        runtime_state = copy.deepcopy(["noise", _LIST_NODE])
        place_metadata = runtime_state[1][8][0][1]
        assert isinstance(place_metadata, list)
        place_metadata[6] = [None]

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(parsed.places[0].cid, None)
        self.assertEqual(parsed.places[0].google_id, "/g/11yakumo")
        self.assertEqual(
            parsed.places[0].maps_url,
            "https://www.google.com/maps/search/?api=1&query=Yakumo%2C+Shibuya%2C+Tokyo",
        )

    def test_builds_coordinate_query_url_only_when_no_name_or_address_exist(self) -> None:
        runtime_state = [
            "noise",
            [
                ["LIST123", 1, None, 1, 1],
                4,
                "https://www.google.com/maps/placelists/list/LIST123",
                "Owner",
                "Untitled",
                None,
                None,
                None,
                [
                    [
                        None,
                        [
                            None,
                            None,
                            None,
                            None,
                            None,
                            [None, None, 35.6501307, 139.6868459],
                            [None],
                            None,
                        ],
                        None,
                        None,
                    ]
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(
            "https://www.google.com/maps/@0,0,3z/data=!4m3!11m2!2sLIST123!3e3",
            runtime_state=runtime_state,
        )

        self.assertEqual(len(parsed.places), 1)
        self.assertEqual(
            parsed.places[0].maps_url,
            "https://www.google.com/maps/search/?api=1&query=35.6501307%2C139.6868459",
        )

    def test_dedupes_places_with_same_cid(self) -> None:
        runtime_state = copy.deepcopy(["noise", _LIST_NODE])
        duplicate_place = copy.deepcopy(runtime_state[1][8][0])
        runtime_state[1][8].append(duplicate_place)

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(len(parsed.places), 2)
        self.assertEqual(parsed.places[0].cid, "7451636382641713350")
        self.assertEqual(parsed.places[1].cid, "1234567890123456789")

    def test_extracts_favorite_and_note_from_user_payload_shape(self) -> None:
        runtime_state = [
            "noise",
            [
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
                            (
                                "Japan, 〒153-0051 Tokyo, Meguro City, Kamimeguro, "
                                "2 Chome−12−2 W.nakameguro 1F"
                            ),
                            [None, None, 35.6426886, 139.6988208],
                            ["6924437575605096209", "-782808945063765017"],
                            "/g/1pty5xgj1",
                        ],
                        "MARU",
                        "Foie gras, caviar and truffle oyakodon!",
                        None,
                        None,
                        None,
                        [[[[3, None, "104356373423434804635", "❤️", [1776133481, 81561000]]]]],
                        [[1], ["6924437575605096209", "-782808945063765017"]],
                        [1776063335, 302383000],
                        [1776132745, 850748000],
                        None,
                    ]
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(len(parsed.places), 1)
        self.assertEqual(parsed.places[0].name, "MARU")
        self.assertEqual(
            parsed.places[0].note,
            "Foie gras, caviar and truffle oyakodon!",
        )
        self.assertTrue(parsed.places[0].is_favorite)

    def test_extracts_favorite_when_place_name_is_missing(self) -> None:
        runtime_state = [
            "noise",
            [
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
                            "Nakameguro, Tokyo",
                            [None, None, 35.6426886, 139.6988208],
                            ["6924437575605096209", "-782808945063765017"],
                            "/g/1pty5xgj1",
                        ],
                        None,
                        None,
                        None,
                        None,
                        None,
                        [[[[3, None, "104356373423434804635", "❤️", [1776133481, 81561000]]]]],
                    ]
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(len(parsed.places), 1)
        self.assertEqual(parsed.places[0].address, "Nakameguro, Tokyo")
        self.assertTrue(parsed.places[0].is_favorite)

    def test_prefers_enclosing_place_name_over_metadata_string(self) -> None:
        runtime_state = [
            "noise",
            [
                ["UGEPbA20Qd-OH4uoWjmDgQ", 1, None, 1, 1],
                4,
                "https://www.google.com/maps/placelists/list/UGEPbA20Qd-OH4uoWjmDgQ",
                "Owner",
                "Taipei Dumplings",
                None,
                None,
                None,
                [
                    [
                        None,
                        [
                            None,
                            None,
                            (
                                "106, Taiwan, Taipei City, Da’an District, Section 4, "
                                "Zhongxiao E Rd, 97號頂好紫琳蒸餃館 Zi Lin Steamed DumplingB1樓"
                            ),
                            None,
                            (
                                "106, Taiwan, Taipei City, Da’an District, Section 4, "
                                "Zhongxiao E Rd, 97號B1樓"
                            ),
                            [None, None, 25.0417836, 121.5479306],
                            ["3765761194353288769", "4471733103496006465"],
                            "/g/1tlcywsb",
                        ],
                        "頂好紫琳蒸餃館 Zi Lin Steamed Dumpling",
                        "",
                        None,
                        None,
                        None,
                        [],
                        [["3765761194353288769", "4471733103496006465"]],
                    ]
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)

        self.assertEqual(len(parsed.places), 1)
        self.assertEqual(parsed.places[0].name, "頂好紫琳蒸餃館 Zi Lin Steamed Dumpling")
        self.assertEqual(
            parsed.places[0].address,
            "106, Taiwan, Taipei City, Da’an District, Section 4, Zhongxiao E Rd, 97號B1樓",
        )

    def test_does_not_promote_list_title_into_place_name(self) -> None:
        runtime_state = [
            "noise",
            [
                ["LIST123", 1, None, 1, 1],
                4,
                "https://www.google.com/maps/placelists/list/LIST123",
                "Owner",
                (
                    "Michael’s Tasmania, Australia 🇦🇺 bookmarks. "
                    "See https://beacons.ai/demflyers for more, or follow on "
                    "Instagram / Threads / BlueSky @demflyers"
                ),
                None,
                None,
                None,
                [
                    [
                        None,
                        [
                            None,
                            None,
                            None,
                            None,
                            "Hive Tasmania",
                            [None, None, -41.157461399999995, 146.1758303],
                            ["104356373423434804635"],
                            None,
                        ],
                        None,
                        None,
                    ]
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(
            "https://www.google.com/maps/@0,0,3z/data=!4m3!11m2!2sLIST123!3e3",
            runtime_state=runtime_state,
        )

        self.assertEqual(len(parsed.places), 1)
        self.assertEqual(parsed.places[0].name, "Hive Tasmania")
        self.assertEqual(parsed.places[0].address, None)
        self.assertEqual(parsed.places[0].cid, "104356373423434804635")

    def test_uses_structured_place_record_for_sparse_real_payload_shape(self) -> None:
        owner = [
            "Michael Wu",
            (
                "https://lh3.googleusercontent.com/a-/ALV-UjW_i8-Eyr6conUhZ6tzGGlFe76mQTGeURI9N"
                "KDlca0FzlN0GY0Kjg"
            ),
            "104356373423434804635",
        ]
        runtime_state = [
            "noise",
            [
                ["LIST123", 1, None, 1, 1],
                4,
                "https://www.google.com/maps/placelists/list/LIST123",
                "Owner",
                "Tasmania, Australia 🇦🇺",
                (
                    "Michael’s Tasmania, Australia 🇦🇺 bookmarks. "
                    "See https://beacons.ai/demflyers for more, or follow on "
                    "Instagram / Threads / BlueSky @demflyers"
                ),
                None,
                None,
                [
                    [
                        None,
                        [
                            None,
                            None,
                            (
                                "The Source, Ether Building, 655 Main Rd, "
                                "Berriedale TAS 7011, Australia"
                            ),
                            None,
                            "Ether Building, 655 Main Rd, Berriedale TAS 7011, Australia",
                            [None, None, -42.811949, 147.2614472],
                            ["-6165976776628271961", "-3752281006438109761"],
                            "/g/11g_1pnk5",
                        ],
                        "The Source",
                        "",
                        None,
                        None,
                        None,
                        [],
                        [[1], ["-6165976776628271961", "-3752281006438109761"]],
                        [1749896974, 425450000],
                        [1749896974, 425450000],
                        None,
                        owner,
                    ],
                    [
                        None,
                        [
                            None,
                            None,
                            "",
                            None,
                            "",
                            [None, None, -41.157461399999995, 146.1758303],
                            ["-6162110142486463501", "-7368952120126222420"],
                        ],
                        "Hive Tasmania",
                        "",
                        None,
                        None,
                        None,
                        [],
                        [[1], ["-6162110142486463501", "-7368952120126222420"]],
                        [1749696774, 839942000],
                        [1749696774, 839942000],
                        None,
                        owner,
                    ],
                ],
            ],
        ]

        parsed = parse_saved_list_artifacts(
            "https://www.google.com/maps/@0,0,3z/data=!4m3!11m2!2sLIST123!3e3",
            runtime_state=runtime_state,
        )

        self.assertEqual(len(parsed.places), 2)
        self.assertEqual(parsed.places[0].name, "The Source")
        self.assertEqual(
            parsed.places[0].address,
            "Ether Building, 655 Main Rd, Berriedale TAS 7011, Australia",
        )
        self.assertEqual(parsed.places[0].cid, "14694463067271441855")
        self.assertEqual(parsed.places[0].google_id, "/g/11g_1pnk5")
        self.assertEqual(parsed.places[1].name, "Hive Tasmania")
        self.assertEqual(parsed.places[1].address, None)
        self.assertEqual(parsed.places[1].cid, "11077791953583329196")
        self.assertEqual(parsed.places[1].google_id, None)
        self.assertNotIn(
            "104356373423434804635",
            {place.cid for place in parsed.places if place.cid is not None},
        )

    def test_raises_when_no_place_records_are_found(self) -> None:
        runtime_state = copy.deepcopy(["noise", _LIST_NODE])
        runtime_state[1][8] = []

        with self.assertRaises(ParseError):
            parse_saved_list_artifacts(_LIST_URL, runtime_state=runtime_state)


if __name__ == "__main__":
    unittest.main()
