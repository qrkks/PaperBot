from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

import pubmed_to_zotero as pz


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

    @patch("pubmed_to_zotero.create_collection")
    @patch("pubmed_to_zotero.list_collections")
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

    @patch("pubmed_to_zotero.create_collection")
    @patch("pubmed_to_zotero.list_collections")
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

    @patch("pubmed_to_zotero.list_collections")
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


class ItemPayloadTests(unittest.TestCase):
    @patch("pubmed_to_zotero.requests.post")
    def test_zotero_create_items_attaches_collection_key(self, mock_post: MagicMock) -> None:
        response = MagicMock()
        response.text = '{"successful":{}}'
        response.json.return_value = {"successful": {"0": {"key": "I1"}}}
        mock_post.return_value = response

        pz.zotero_create_items(
            library_type="users",
            library_id="123",
            api_key="k",
            items=[{"title": "Paper 1"}],
            collection_key="COLL123",
        )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload[0]["collections"], ["COLL123"])


if __name__ == "__main__":
    unittest.main()
