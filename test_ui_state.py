import unittest

from paperbot.ui_state import (
    coerce_history_entry_id,
    coerce_selectbox_value,
    format_history_entry_label,
)


class UIStateTests(unittest.TestCase):
    def test_coerce_selectbox_value_keeps_valid_selection(self) -> None:
        self.assertEqual(
            coerce_selectbox_value("b", ["", "a", "b"], default=""),
            "b",
        )

    def test_coerce_selectbox_value_falls_back_to_default(self) -> None:
        self.assertEqual(
            coerce_selectbox_value("missing", ["", "a", "b"], default=""),
            "",
        )

    def test_coerce_history_entry_id_rejects_removed_entry(self) -> None:
        history_entries = [
            {"id": "hist-1", "created_at": "2026-04-13 10:00:00"},
            {"id": "hist-2", "created_at": "2026-04-13 11:00:00"},
        ]
        self.assertEqual(coerce_history_entry_id("hist-3", history_entries), "")

    def test_format_history_entry_label_uses_core_fields(self) -> None:
        entry = {
            "id": "hist-1",
            "created_at": "2026-04-13 10:00:00",
            "event_type": "preview",
            "query": "financial toxicity",
        }
        self.assertEqual(
            format_history_entry_label(entry),
            "2026-04-13 10:00:00 | preview | financial toxicity",
        )


if __name__ == "__main__":
    unittest.main()
