from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import paperbot.core as pz


class CollectionTests(unittest.TestCase):
    def test_validate_library_id_rejects_email(self) -> None:
        with self.assertRaises(ValueError):
            pz.validate_library_id("users", "258458003@qq.com")

    def test_validate_library_id_accepts_numeric(self) -> None:
        self.assertEqual(pz.validate_library_id("users", "258458003"), "258458003")

    def test_build_collection_paths_with_same_leaf_names(self) -> None:
        collections = [
            {"key": "A", "data": {"name": "ProjectA", "parentCollection": ""}},
            {"key": "B", "data": {"name": "Review", "parentCollection": "A"}},
            {"key": "C", "data": {"name": "ProjectB", "parentCollection": ""}},
            {"key": "D", "data": {"name": "Review", "parentCollection": "C"}},
            {"key": "E", "data": {"name": "Sub", "parentCollection": "B"}},
        ]

        mapping = pz.build_collection_paths(collections)

        self.assertEqual(mapping["ProjectA/Review"], "B")
        self.assertEqual(mapping["ProjectB/Review"], "D")
        self.assertEqual(mapping["ProjectA/Review/Sub"], "E")

    def test_find_ambiguous_collection_paths(self) -> None:
        collections = [
            {"key": "A1", "data": {"name": "test", "parentCollection": False}},
            {"key": "A2", "data": {"name": "test", "parentCollection": False}},
            {"key": "B", "data": {"name": "unique", "parentCollection": False}},
        ]

        ambiguous = pz.find_ambiguous_collection_paths(collections)
        mapping = pz.build_collection_paths(collections)

        self.assertEqual(ambiguous["test"], ["A1", "A2"])
        self.assertNotIn("test", mapping)
        self.assertEqual(mapping["unique"], "B")

    @patch("paperbot.core.create_collection")
    @patch("paperbot.core.list_collections")
    def test_ensure_collection_path_existing_only(self, mock_list: MagicMock, mock_create: MagicMock) -> None:
        mock_list.return_value = [
            {"key": "A", "data": {"name": "ProjectA", "parentCollection": ""}},
            {"key": "B", "data": {"name": "Review", "parentCollection": "A"}},
        ]

        key = pz.ensure_collection_path(
            library_type="users",
            library_id="123",
            api_key="k",
            collection_path="ProjectA/Review",
            auto_create=True,
        )

        self.assertEqual(key, "B")
        mock_create.assert_not_called()

    @patch("paperbot.core.create_collection")
    @patch("paperbot.core.list_collections")
    def test_ensure_collection_path_existing_root_collection_with_false_parent(
        self,
        mock_list: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        mock_list.return_value = [
            {"key": "ROOT1", "data": {"name": "test", "parentCollection": False}},
        ]

        key = pz.ensure_collection_path(
            library_type="users",
            library_id="123456",
            api_key="api-key",
            collection_path="test",
        )

        self.assertEqual(key, "ROOT1")
        mock_create.assert_not_called()

    @patch("paperbot.core.create_collection")
    @patch("paperbot.core.list_collections")
    def test_ensure_collection_path_partial_create(self, mock_list: MagicMock, mock_create: MagicMock) -> None:
        mock_list.return_value = [
            {"key": "A", "data": {"name": "ProjectA", "parentCollection": ""}},
        ]
        mock_create.side_effect = ["B", "C"]

        key = pz.ensure_collection_path(
            library_type="users",
            library_id="123",
            api_key="k",
            collection_path="ProjectA/Review/Sub",
            auto_create=True,
        )

        self.assertEqual(key, "C")
        self.assertEqual(
            mock_create.call_args_list,
            [
                call(
                    library_type="users",
                    library_id="123",
                    api_key="k",
                    name="Review",
                    parent_collection="A",
                ),
                call(
                    library_type="users",
                    library_id="123",
                    api_key="k",
                    name="Sub",
                    parent_collection="B",
                ),
            ],
        )

    @patch("paperbot.core.list_collections")
    def test_ensure_collection_path_missing_without_auto_create_raises(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {"key": "A", "data": {"name": "ProjectA", "parentCollection": ""}},
        ]

        with self.assertRaises(RuntimeError):
            pz.ensure_collection_path(
                library_type="users",
                library_id="123",
                api_key="k",
                collection_path="ProjectA/Review",
                auto_create=False,
            )

    @patch("paperbot.core.list_collections")
    def test_ensure_collection_path_ambiguous_raises(self, mock_list: MagicMock) -> None:
        mock_list.return_value = [
            {"key": "A1", "data": {"name": "test", "parentCollection": False}},
            {"key": "A2", "data": {"name": "test", "parentCollection": False}},
        ]

        with self.assertRaises(RuntimeError) as ctx:
            pz.ensure_collection_path(
                library_type="users",
                library_id="123",
                api_key="k",
                collection_path="test",
                auto_create=True,
            )

        self.assertIn("ambiguous", str(ctx.exception).lower())


class ItemPayloadTests(unittest.TestCase):
    @patch("paperbot.core.requests.post")
    def test_zotero_create_items_attaches_collection_key(self, mock_post: MagicMock) -> None:
        response = MagicMock()
        response.text = '{"successful":{}}'
        response.json.return_value = {"successful": {"0": {"key": "I1"}}}
        mock_post.return_value = response

        pz.zotero_create_items(
            library_type="users",
            library_id="123",
            api_key="k",
            items=[{"title": "Paper 1", "_metric_citation_count": 10}],
            collection_key="COLL123",
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload[0]["collections"], ["COLL123"])
        self.assertNotIn("_metric_citation_count", payload[0])


class MetricTests(unittest.TestCase):
    def test_apply_secondary_metrics_writes_extra(self) -> None:
        records = [{"title": "Paper 1", "PMID": "123", "extra": "PMID: 123"}]
        metrics = {
            "123": {
                "citation_count": 42,
                "journal_metric_2yr_mean_citedness": 3.5,
                "source_name": "Test Journal",
            }
        }

        pz.apply_secondary_metrics_to_records(
            records=records,
            metrics_by_pmid=metrics,
            secondary_sort="citation_count_desc",
            attach_to_extra=True,
            snapshot_date="2026-04-11",
        )

        extra = records[0]["extra"]
        self.assertIn("OpenAlex Cited By: 42", extra)
        self.assertIn("OpenAlex Journal 2yr Mean Citedness: 3.5000", extra)
        self.assertIn("Secondary Sort: citation_count_desc", extra)
        self.assertIn("Metrics Snapshot Date: 2026-04-11", extra)

    @patch("paperbot.core.fetch_openalex_source_metrics_by_issn")
    def test_backfill_journal_metrics_from_record_issns(
        self, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = {"1234-5678": 2.75}
        records = [
            {"PMID": "123", "ISSN": "1234-5678"},
            {"PMID": "456", "ISSN": "9999-9999"},
        ]
        metrics = {
            "123": {"citation_count": 10, "journal_metric_2yr_mean_citedness": None},
            "456": {"citation_count": 5, "journal_metric_2yr_mean_citedness": 1.5},
        }

        updated = pz.backfill_journal_metrics_from_record_issns(records, metrics)

        self.assertEqual(updated["123"]["journal_metric_2yr_mean_citedness"], 2.75)
        self.assertEqual(updated["456"]["journal_metric_2yr_mean_citedness"], 1.5)
        mock_fetch.assert_called_once_with(
            issns=["1234-5678"],
            email=None,
            api_key=None,
        )

    def test_secondary_sort_records_by_citation(self) -> None:
        records = [
            {"PMID": "1", "_metric_citation_count": 10},
            {"PMID": "2", "_metric_citation_count": 30},
            {"PMID": "3", "_metric_citation_count": 20},
        ]
        sorted_records = pz.secondary_sort_records(records, "citation_count_desc")
        self.assertEqual([r["PMID"] for r in sorted_records], ["2", "3", "1"])

    def test_filter_duplicate_records(self) -> None:
        records = [
            {"title": "A", "DOI": "10.1000/abc", "PMID": "1"},
            {"title": "B", "DOI": "10.1000/abc", "PMID": "2"},
            {"title": "C", "DOI": "10.1000/new", "PMID": "3"},
            {"title": "D", "DOI": "", "PMID": "5"},
        ]
        filtered, skipped_existing, skipped_incoming = pz.filter_duplicate_records(
            records,
            existing_dois={"10.1000/new"},
            existing_pmids={"5"},
        )
        self.assertEqual([row["title"] for row in filtered], ["A"])
        self.assertEqual(skipped_existing, 2)
        self.assertEqual(skipped_incoming, 1)

    def test_plan_record_import_actions_links_existing_to_collection(self) -> None:
        records = [{"title": "A", "DOI": "10.1000/abc", "PMID": "1"}]
        existing_items = [
            {
                "key": "ITEM1",
                "version": 5,
                "collections": ["OTHER"],
                "DOI": "10.1000/abc",
                "PMID": "1",
            }
        ]

        action_records, display_records, skipped_existing, skipped_incoming, linked = (
            pz.plan_record_import_actions(
                records,
                existing_items=existing_items,
                target_collection_key="TARGET",
                target_collection_requested=True,
            )
        )

        self.assertEqual(len(action_records), 1)
        self.assertEqual(display_records[0]["_planned_action"], "link")
        self.assertEqual(display_records[0]["_dedup_status"], "existing_add_to_collection")
        self.assertEqual(display_records[0]["_existing_item_key"], "ITEM1")
        self.assertEqual(skipped_existing, 0)
        self.assertEqual(skipped_incoming, 0)
        self.assertEqual(linked, 1)

    def test_plan_record_import_actions_skips_when_already_in_target_collection(self) -> None:
        records = [{"title": "A", "DOI": "10.1000/abc", "PMID": "1"}]
        existing_items = [
            {
                "key": "ITEM1",
                "version": 5,
                "collections": ["TARGET"],
                "DOI": "10.1000/abc",
                "PMID": "1",
            }
        ]

        action_records, display_records, skipped_existing, skipped_incoming, linked = (
            pz.plan_record_import_actions(
                records,
                existing_items=existing_items,
                target_collection_key="TARGET",
                target_collection_requested=True,
            )
        )

        self.assertEqual(len(action_records), 0)
        self.assertEqual(display_records[0]["_planned_action"], "skip")
        self.assertEqual(display_records[0]["_dedup_status"], "duplicate_existing")
        self.assertEqual(skipped_existing, 1)
        self.assertEqual(skipped_incoming, 0)
        self.assertEqual(linked, 0)

    def test_plan_record_import_actions_collection_scope_links_item_outside_target(self) -> None:
        records = [{"title": "A", "DOI": "10.1000/abc", "PMID": "1"}]
        existing_items = [
            {
                "key": "ITEM1",
                "version": 5,
                "collections": ["OTHER"],
                "DOI": "10.1000/abc",
                "PMID": "1",
            }
        ]

        action_records, display_records, skipped_existing, skipped_incoming, linked = (
            pz.plan_record_import_actions(
                records,
                existing_items=existing_items,
                target_collection_key="TARGET",
                target_collection_requested=True,
                duplicate_scope="collection",
            )
        )

        self.assertEqual(len(action_records), 1)
        self.assertEqual(display_records[0]["_planned_action"], "link")
        self.assertEqual(display_records[0]["_dedup_status"], "existing_add_to_collection")
        self.assertEqual(skipped_existing, 0)
        self.assertEqual(skipped_incoming, 0)
        self.assertEqual(linked, 1)

    def test_plan_record_import_actions_collection_scope_skips_item_in_target(self) -> None:
        records = [{"title": "A", "DOI": "10.1000/abc", "PMID": "1"}]
        existing_items = [
            {
                "key": "ITEM1",
                "version": 5,
                "collections": ["TARGET"],
                "DOI": "10.1000/abc",
                "PMID": "1",
            }
        ]

        action_records, display_records, skipped_existing, skipped_incoming, linked = (
            pz.plan_record_import_actions(
                records,
                existing_items=existing_items,
                target_collection_key="TARGET",
                target_collection_requested=True,
                duplicate_scope="collection",
            )
        )

        self.assertEqual(len(action_records), 0)
        self.assertEqual(display_records[0]["_planned_action"], "skip")
        self.assertEqual(display_records[0]["_dedup_status"], "duplicate_existing")
        self.assertEqual(skipped_existing, 1)
        self.assertEqual(skipped_incoming, 0)
        self.assertEqual(linked, 0)

    @patch("paperbot.core.requests.get")
    def test_list_existing_identifiers(self, mock_get: MagicMock) -> None:
        response1 = MagicMock()
        response1.json.return_value = [
            {"data": {"DOI": "10.1000/xyz", "extra": "PMID: 123"}},
            {"data": {"DOI": "", "PMID": "456"}},
        ]
        response2 = MagicMock()
        response2.json.return_value = []
        mock_get.side_effect = [response1, response2]

        dois, pmids = pz.list_existing_identifiers(
            library_type="users",
            library_id="123",
            api_key="k",
        )

        self.assertIn("10.1000/xyz", dois)
        self.assertIn("123", pmids)
        self.assertIn("456", pmids)

    @patch("paperbot.core.requests.post")
    def test_zotero_link_existing_items_to_collection(self, mock_post: MagicMock) -> None:
        response = MagicMock()
        response.text = '{"successful":{"0":{"key":"ITEM1"}}}'
        response.json.return_value = {"successful": {"0": {"key": "ITEM1"}}}
        mock_post.return_value = response

        pz.zotero_link_existing_items_to_collection(
            library_type="users",
            library_id="123",
            api_key="k",
            collection_key="TARGET",
            items=[
                {
                    "_existing_item_key": "ITEM1",
                    "_existing_item_version": 9,
                    "_existing_item_collections": ["OTHER"],
                }
            ],
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload[0]["key"], "ITEM1")
        self.assertEqual(payload[0]["version"], 9)
        self.assertIn("TARGET", payload[0]["collections"])

    @patch("paperbot.core._request")
    def test_search_pubmed_ids_passes_sort(self, mock_request: MagicMock) -> None:
        response = MagicMock()
        response.json.return_value = {"esearchresult": {"idlist": ["1"]}}
        mock_request.return_value = response

        pz.search_pubmed_ids(
            query="glioblastoma",
            retmax=1,
            email=None,
            api_key=None,
            sort="pub_date",
        )

        params = mock_request.call_args.args[1]
        self.assertEqual(params["sort"], "pub_date")

    @patch("paperbot.core.requests.get")
    def test_fetch_openalex_metrics_uses_valid_select_fields(self, mock_get: MagicMock) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678"},
                    "cited_by_count": 12,
                    "primary_location": {
                        "source": {
                            "display_name": "Journal A",
                            "issn_l": "1234-5678",
                            "summary_stats": {"2yr_mean_citedness": 2.5},
                        }
                    },
                }
            ]
        }
        mock_get.return_value = response

        metrics = pz.fetch_openalex_metrics_by_pmids(["12345678"])

        self.assertIn("12345678", metrics)
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["select"], "id,ids,cited_by_count,primary_location")


if __name__ == "__main__":
    unittest.main()
