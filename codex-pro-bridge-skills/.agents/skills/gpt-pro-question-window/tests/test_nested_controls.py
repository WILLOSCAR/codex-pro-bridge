"""Nested menu sequencing and slider failure boundaries."""
import sys
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS.parents[1] / ".shared")]
from bridge_store import BridgeError
from bridge_runtime.browser import Browser, unique_uid


class NestedControlsTest(unittest.TestCase):
    def test_ready_attachment_requires_one_exact_idle_card(self):
        b = Browser(Mock(), {})
        for cards, expected in [([], False), ([{"name":"x.zip","busy":False}], True),
                ([{"name":"prefix-x.zip","busy":False}], False),
                ([{"name":"x.zip","busy":True}], False),
                ([{"name":"x.zip","busy":False}]*2, False)]:
            b.attachments = Mock(return_value=cards)
            self.assertEqual(b.attachment_ready("x.zip"), expected)

    def test_recovery_observes_but_never_uploads(self):
        from bridge_store import file_sha256
        from devtools_upload import build_upload_action_plan
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "bundle.zip"
            source.write_bytes(b"test evidence")
            sha = file_sha256(source)
            b = Browser(Mock(), {})
            b.page_id, b.owner, b.url = 7, "owner", "https://chatgpt.com/"
            b.assert_identity = Mock()
            b.attachments = Mock(return_value=[{"name":"bundle.zip","busy":False}])
            plan = build_upload_action_plan(page_id="7", attachment_button_uid="1", menu_snapshot_uid="s",
                menu_item_uid="2", windows_path=r"C:\BridgeTest\bundle.zip", staged_sha256=sha)
            plan_path, result_path = root / "plan.json", root / "result.json"
            plan_path.write_text(json.dumps(plan))
            transport = {"status":"accepted","page_id":"7","owner":b.owner,"url":b.url,"plan":plan}
            result_path.with_suffix(".transport.json").write_text(json.dumps(transport))
            staging = {"staged_browser_path":plan["windows_path"], "source_sha256":sha,
                "attachment_name":"bundle.zip", "source_path":str(source), "staged_execution_path":str(source)}
            self.assertTrue(b.recover_upload(staging, plan_path, result_path, Mock())["attachment_chip"])
            b.client.call.assert_not_called()
            b.owner = "another-owner"
            with self.assertRaisesRegex(BridgeError, "identity"):
                b.recover_upload(staging, plan_path, result_path, Mock())

    def test_control_readiness_does_not_repeat_click(self):
        b = Browser(Mock(), {})
        b.page_id = 7
        b.assert_identity = Mock()
        b.snapshot = Mock(side_effect=['uid=1 generic', 'uid=2 button "Model"'])
        with patch("bridge_runtime.browser.time.sleep"):
            b.click(["Model"])
        b.client.call.assert_called_once_with("click",pageId=7,uid="2")

    def test_pending_or_placeholder_label_is_not_a_confirmation(self):
        b = Browser(Mock(), {"thinking":"#thinking","thinking_placeholder_labels":["思考强度"]})
        b.assert_identity = Mock()
        b.evaluate = Mock(side_effect=[{"__pending__":"thinking","count":0},
                                      {"thinking":"思考强度"},{"thinking":"极高"}])
        with patch("bridge_runtime.browser.time.sleep"):
            self.assertEqual(b.read_labels(("thinking",)),{"thinking":"极高"})
        self.assertEqual(b.evaluate.call_count,3)

    def test_ambiguous_labels_fail_without_polling(self):
        b = Browser(Mock(), {"thinking":"#thinking"})
        b.assert_identity = Mock()
        b.evaluate = Mock(return_value={"__pending__":"thinking","count":2})
        with self.assertRaisesRegex(BridgeError,"multiple"):
            b.read_labels(("thinking",))
        b.evaluate.assert_called_once()

    def test_static_upload_label_goes_directly_to_upload_tool(self):
        b = Browser(Mock(), {"attachment_labels":["Add files"],"upload_labels":["从电脑上传"],
                            "attachment_chip":"#chip"})
        b.page_id = 7
        b.assert_identity = Mock()
        b.snapshot = Mock(side_effect=['uid=1 button "Add files"', 'uid=2 StaticText "从电脑上传"'])
        b.evaluate = Mock(return_value=[{"name":"bundle.zip","busy":False}])
        b.upload({"staged_browser_path":r"C:\BridgeTest\bundle.zip","source_sha256":"a"*64,
                  "attachment_name":"bundle.zip"},Path("plan"),Path("result"),Mock(),Mock())
        self.assertEqual([c.args[0] for c in b.client.call.call_args_list],["click","upload_file"])
        self.assertEqual(b.client.call.call_args_list[1].kwargs["uid"],"2")

    def browser(self, initial="高", stuck=False, focused=True):
        b = Browser(Mock(), {"control_layout":"nested-slider", "thinking_menu_labels":["高","极高"],
            "model_menu_labels":["选择模型"], "thinking_positions":{"极高":3},
            "thinking_slider":"#slider", "thinking_keyboard_control":"#keys"})
        b.page_id, b.owner, b.url = 1, "owner", "https://chatgpt.com/g/g-p-test/project"
        state = {"value":3 if initial == "极高" else 2}
        b.assert_identity = Mock()
        b.click = Mock()
        b.read_labels = Mock(side_effect=lambda keys: {k:("最新" if k == "model" else
            ("极高" if state["value"] == 3 else "高")) for k in keys})
        b.evaluate = Mock(side_effect=lambda script: focused if "nodes[0].focus()" in script else
                          {"min":0,"max":4,"value":state["value"]})
        def press(key):
            if key == "ArrowRight" and not stuck:
                state["value"] += 1
        b.press = Mock(side_effect=press)
        return b

    def request(self):
        return {"requested_model":"最新","model_selection_kind":"latest-alias",
                "requested_thinking_intensity":"极高"}

    def test_one_step_and_no_reselection(self):
        b = self.browser()
        receipt = b.adjust_controls(self.request())
        self.assertEqual(receipt["counts"]["model_selection"], 0)
        self.assertEqual(receipt["counts"]["thinking_adjustment"], 1)
        self.assertEqual(receipt["counts"]["model_menu_open"], 2)
        self.assertEqual([c.args[0] for c in b.press.call_args_list].count("ArrowRight"), 1)

    def test_already_matching_does_not_touch_slider(self):
        b = self.browser(initial="极高")
        receipt = b.adjust_controls(self.request())
        self.assertEqual(receipt["counts"]["thinking_control_open"], 2)
        b.evaluate.assert_not_called()

    def test_stuck_slider_is_not_retried(self):
        b = self.browser(stuck=True)
        with self.assertRaisesRegex(BridgeError,"exactly one step"):
            b.adjust_controls(self.request())
        self.assertEqual([c.args[0] for c in b.press.call_args_list].count("ArrowRight"), 1)

    def test_failed_focus_never_presses_arrow(self):
        b = self.browser(focused=False)
        with self.assertRaisesRegex(BridgeError,"receive focus"):
            b.adjust_controls(self.request())
        self.assertNotIn("ArrowRight", [c.args[0] for c in b.press.call_args_list])

    def test_native_disabled_snapshot_is_not_clickable(self):
        with self.assertRaises(BridgeError):
            unique_uid('uid=1 button "Send" disableable disabled', ("button",), ["Send"])


if __name__ == "__main__":
    unittest.main()
