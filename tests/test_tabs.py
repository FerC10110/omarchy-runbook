import io
import json
import os
import tempfile
import unittest

from _load import load
from fakes import FakeRun

engine = load()

TAB = "a" * 32
OTHER = "b" * 32


def script(name, tab=None):
    fields = {"name": name, "command": "true", "help": ""}
    if tab is not None:
        fields["tab"] = tab
    return fields


def names(library, tab=engine.MAIN_TAB):
    return [item.get("name", "—" + item.get("label", "")) for item in library["scripts"]
            if item["tab"] == tab]


class LibraryShapeTest(unittest.TestCase):
    def test_empty_library_has_the_main_tab(self):
        self.assertEqual(engine.empty_library()["tabs"], [{"id": "main", "name": "Mine"}])

    def test_old_file_without_tabs_gets_the_main_tab_and_keeps_its_scripts(self):
        library = engine._normalise({"scripts": [dict(script("Ports"), id="1" * 32)]})
        self.assertEqual(library["tabs"], [{"id": "main", "name": "Mine"}])
        self.assertEqual(library["scripts"][0]["tab"], "main")

    def test_main_tab_is_added_when_a_file_lacks_it(self):
        library = engine._normalise({"tabs": [{"id": TAB, "name": "Docker"}]})
        self.assertEqual([t["id"] for t in library["tabs"]], ["main", TAB])

    def test_tab_order_and_names_are_kept(self):
        tabs = [{"id": TAB, "name": "Docker"}, {"id": "main", "name": "General"}]
        self.assertEqual(engine._normalise({"tabs": tabs})["tabs"], tabs)

    def test_items_in_a_missing_or_malformed_tab_fall_back_to_main(self):
        raw = {"tabs": [{"id": TAB, "name": "Docker"}], "scripts": [
            dict(script("A", tab=OTHER), id="1" * 32),
            dict(script("B"), id="2" * 32, tab=["x"]),
            dict(script("C", tab=TAB), id="3" * 32)]}
        library = engine._normalise(raw)
        self.assertEqual([s["tab"] for s in library["scripts"]], ["main", "main", TAB])

    def test_invalid_tabs_are_errors(self):
        for tabs in ("x", [1], [{"id": "Bad", "name": "x"}], [{"id": TAB, "name": ""}],
                     [{"id": TAB, "name": "a"}, {"id": TAB, "name": "b"}]):
            with self.assertRaises(engine.RunbookError, msg=repr(tabs)):
                engine._normalise({"tabs": tabs})

    def test_separator_round_trips(self):
        raw = {"scripts": [{"id": "1" * 32, "kind": "separator", "label": "docker", "tab": "main"}]}
        self.assertEqual(engine._normalise(raw)["scripts"],
                         [{"kind": "separator", "label": "docker", "tab": "main", "id": "1" * 32}])


class SeparatorTest(unittest.TestCase):
    def test_label_is_optional_and_trimmed(self):
        self.assertEqual(engine.validate_separator({}), {"kind": "separator", "label": ""})
        self.assertEqual(engine.validate_separator({"label": "  docker "})["label"], "docker")

    def test_label_limits(self):
        for label in ("a\nb", "x" * (engine.LABEL_MAX + 1), 3):
            with self.assertRaises(engine.RunbookError, msg=repr(label)):
                engine.validate_separator({"label": label})

    def test_separators_do_not_take_part_in_unique_names(self):
        library = engine.empty_library()
        engine.add_script(library, {"kind": "separator", "label": "Ports"})
        engine.add_script(library, {"kind": "separator", "label": ""})
        engine.add_script(library, script("Ports"))
        self.assertEqual(len(library["scripts"]), 3)


class AddTest(unittest.TestCase):
    def setUp(self):
        self.library = engine.empty_library()
        self.library["tabs"].append({"id": TAB, "name": "Docker"})

    def test_new_items_go_to_the_main_tab_by_default(self):
        self.assertEqual(engine.add_script(self.library, script("A"))["tab"], "main")

    def test_new_items_go_to_the_tab_they_name(self):
        self.assertEqual(engine.add_script(self.library, script("A", tab=TAB))["tab"], TAB)

    def test_unknown_or_malformed_tab_is_an_error(self):
        for tab in (OTHER, "Bad!", 3):
            with self.assertRaises(engine.RunbookError, msg=repr(tab)):
                engine.add_script(self.library, script("A", tab=tab))
        self.assertEqual(self.library["scripts"], [])

    def test_after_inserts_right_below_that_item(self):
        a = engine.add_script(self.library, script("A"))
        engine.add_script(self.library, script("B"))
        engine.add_script(self.library, {"kind": "separator", "label": "x"}, after=a["id"])
        self.assertEqual(names(self.library), ["A", "—x", "B"])

    def test_unknown_after_appends(self):
        engine.add_script(self.library, script("A"))
        engine.add_script(self.library, script("B"), after="f" * 32)
        self.assertEqual(names(self.library), ["A", "B"])


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.library = engine.empty_library()
        self.library["tabs"].append({"id": TAB, "name": "Docker"})

    def test_update_moves_a_script_to_another_tab(self):
        item = engine.add_script(self.library, script("A"))
        engine.update_script(self.library, item["id"], script("A", tab=TAB))
        self.assertEqual(item["tab"], TAB)

    def test_update_without_a_tab_keeps_it(self):
        item = engine.add_script(self.library, script("A", tab=TAB))
        engine.update_script(self.library, item["id"], script("A2"))
        self.assertEqual((item["name"], item["tab"]), ("A2", TAB))

    def test_update_to_an_unknown_tab_is_an_error(self):
        item = engine.add_script(self.library, script("A"))
        with self.assertRaises(engine.RunbookError):
            engine.update_script(self.library, item["id"], script("A", tab=OTHER))

    def test_update_a_separator_label(self):
        item = engine.add_script(self.library, {"kind": "separator", "label": "x"})
        engine.update_script(self.library, item["id"], {"label": "docker"})
        self.assertEqual((item["kind"], item["label"]), ("separator", "docker"))

    def test_the_separator_form_changes_its_tab(self):
        item = engine.add_script(self.library, {"kind": "separator", "label": "x"})
        engine.update_script(self.library, item["id"], {"kind": "separator", "label": "x", "tab": TAB})
        self.assertEqual((item["kind"], item["tab"]), ("separator", TAB))


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.library = engine.empty_library()
        self.library["tabs"].append({"id": TAB, "name": "Docker"})
        self.a = engine.add_script(self.library, script("A"))
        self.x = engine.add_script(self.library, script("X", tab=TAB))
        self.b = engine.add_script(self.library, script("B"))
        self.c = engine.add_script(self.library, script("C"))

    def test_moves_past_items_of_other_tabs(self):
        engine.move_script(self.library, self.b["id"], -1)
        self.assertEqual(names(self.library), ["B", "A", "C"])
        self.assertEqual(names(self.library, TAB), ["X"])

    def test_moving_down(self):
        engine.move_script(self.library, self.a["id"], 1)
        self.assertEqual(names(self.library), ["B", "A", "C"])

    def test_edges_do_nothing(self):
        engine.move_script(self.library, self.a["id"], -1)
        engine.move_script(self.library, self.c["id"], 1)
        self.assertEqual(names(self.library), ["A", "B", "C"])

    def test_unknown_item(self):
        with self.assertRaises(engine.RunbookError):
            engine.move_script(self.library, "f" * 32, 1)


class TabTest(unittest.TestCase):
    def setUp(self):
        self.library = engine.empty_library()

    def test_add_tab(self):
        tab = engine.add_tab(self.library, {"name": " Docker "})
        self.assertRegex(tab["id"], r"^[0-9a-f]{32}$")
        self.assertEqual([t["name"] for t in self.library["tabs"]], ["Mine", "Docker"])

    def test_tab_names_are_unique_ignoring_case(self):
        with self.assertRaisesRegex(engine.RunbookError, "already exists"):
            engine.add_tab(self.library, {"name": "mine"})

    def test_tab_name_limits(self):
        for name in ("", "a\nb", "x" * (engine.TAB_NAME_MAX + 1)):
            with self.assertRaises(engine.RunbookError, msg=repr(name)):
                engine.add_tab(self.library, {"name": name})

    def test_rename_tab_including_main(self):
        engine.rename_tab(self.library, "main", {"name": "General"})
        self.assertEqual(self.library["tabs"][0], {"id": "main", "name": "General"})

    def test_rename_keeps_its_own_name_case_change(self):
        engine.rename_tab(self.library, "main", {"name": "MINE"})
        self.assertEqual(self.library["tabs"][0]["name"], "MINE")

    def test_remove_tab_moves_its_items_to_main(self):
        tab = engine.add_tab(self.library, {"name": "Docker"})
        item = engine.add_script(self.library, script("A", tab=tab["id"]))
        engine.remove_tab(self.library, tab["id"])
        self.assertEqual([t["id"] for t in self.library["tabs"]], ["main"])
        self.assertEqual(item["tab"], "main")

    def test_main_tab_cannot_be_removed(self):
        with self.assertRaisesRegex(engine.RunbookError, "cannot be deleted"):
            engine.remove_tab(self.library, "main")

    def test_move_tab(self):
        tab = engine.add_tab(self.library, {"name": "Docker"})
        engine.move_tab(self.library, tab["id"], -1)
        self.assertEqual([t["id"] for t in self.library["tabs"]], [tab["id"], "main"])
        engine.move_tab(self.library, tab["id"], -1)
        self.assertEqual([t["id"] for t in self.library["tabs"]], [tab["id"], "main"])

    def test_unknown_tab(self):
        for call in (lambda: engine.rename_tab(self.library, TAB, {"name": "x"}),
                     lambda: engine.remove_tab(self.library, TAB),
                     lambda: engine.move_tab(self.library, TAB, 1)):
            with self.assertRaisesRegex(engine.RunbookError, "No such tab"):
                call()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": "/h", "PATH": "/usr/bin", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_TMUX_SOCKET": "sock", "RUNBOOK_TMUX_CONF": "/plug/tmux.conf"}
        self.path = os.path.join(self.tmp.name, "runbook", "scripts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, argv, stdin=None):
        fake = FakeRun()
        out = io.StringIO()
        text = json.dumps(stdin) if stdin is not None else ""
        code = engine.main(argv, environ=self.environ, run=fake, stdin=io.StringIO(text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0])


class TabCliTest(CliCase):
    def test_tab_commands_round_trip_through_the_file(self):
        code, payload = self.cli(["tab-add"], {"name": "Docker"})
        self.assertEqual(code, 0)
        tab_id = payload["tabs"][1]["id"]
        self.cli(["tab-rename", tab_id], {"name": "Containers"})
        self.cli(["tab-move", tab_id], {"delta": -1})
        code, payload = self.cli(["list"])
        self.assertEqual(payload["tabs"], [{"id": tab_id, "name": "Containers"},
                                           {"id": "main", "name": "Mine"}])
        code, payload = self.cli(["tab-remove", tab_id])
        self.assertEqual((code, payload["tabs"]), (0, [{"id": "main", "name": "Mine"}]))

    def test_tab_commands_check_the_tab_id_first(self):
        for cmd in ("tab-rename", "tab-remove", "tab-move"):
            self.assertEqual(self.cli([cmd, "Bad!"], {}), (1, {"error": "Invalid tab id"}), cmd)
            self.assertEqual(self.cli([cmd]), (1, {"error": "Missing tab id"}), cmd)
        self.assertFalse(os.path.exists(self.path))

    def test_bad_delta(self):
        for delta in (0, 2, True, "1", None):
            code, payload = self.cli(["tab-move", "main"], {"delta": delta})
            self.assertEqual((code, payload), (1, {"error": "Delta must be -1 or 1"}), repr(delta))

    def test_add_separator_after_and_move(self):
        _, payload = self.cli(["add"], script("A"))
        a = payload["scripts"][0]["id"]
        self.cli(["add"], script("B"))
        _, payload = self.cli(["add"], {"kind": "separator", "label": "docker", "after": a})
        self.assertEqual([s.get("name", s.get("label")) for s in payload["scripts"]], ["A", "docker", "B"])
        sep = payload["scripts"][1]["id"]
        _, payload = self.cli(["move", sep], {"delta": 1})
        self.assertEqual([s.get("name", s.get("label")) for s in payload["scripts"]], ["A", "B", "docker"])

    def test_move_checks_the_id_first(self):
        self.assertEqual(self.cli(["move", "bad"], {"delta": 1}), (1, {"error": "Invalid script id"}))

    def test_a_separator_cannot_run(self):
        _, payload = self.cli(["add"], {"kind": "separator"})
        code, payload = self.cli(["run", payload["scripts"][0]["id"]])
        self.assertEqual((code, payload), (1, {"error": "A separator cannot run"}))


class ConfigTest(CliCase):
    def test_omarchy_tab_is_on_by_default_and_can_be_turned_off(self):
        self.assertTrue(self.cli(["config"])[1]["omarchy"]["enabled"])
        code, payload = self.cli(["set-config"], {"omarchy": {"enabled": False}})
        self.assertEqual((code, payload["omarchy"]), (0, {"enabled": False}))
        self.assertFalse(self.cli(["config"])[1]["omarchy"]["enabled"])

    def test_bad_values_repair(self):
        repaired = engine._repair_config({"readily": {"tab": "Bad!"}, "omarchy": {"enabled": "no"}})
        self.assertEqual(repaired, engine.default_config())

    def test_readily_tab(self):
        _, payload = self.cli(["set-config"], {"readily": {"tab": TAB}})
        self.assertEqual(payload["readily"], {"enabled": False, "tab": TAB})


class ReadilyTabTest(CliCase):
    def rows(self, tab_config, tabs):
        engine.save_config(self.environ, {"readily": {"enabled": True, "tab": tab_config}})
        library = engine.empty_library()
        library["tabs"] += tabs
        original = engine.read_readily_commands
        engine.read_readily_commands = lambda environ, run: {
            "scripts": [{"name": "R", "command": "true", "help": "", "id": "9" * 32,
                         "source": "readily", "readonly": True}],
            "skipped": 0, "warning": None, "ok": True}
        try:
            return engine.merge_readily(library, self.environ, FakeRun())["scripts"]
        finally:
            engine.read_readily_commands = original

    def test_readily_rows_go_to_the_configured_tab(self):
        self.assertEqual(self.rows(TAB, [{"id": TAB, "name": "Obsidian"}])[0]["tab"], TAB)

    def test_readily_rows_fall_back_to_main_when_the_tab_is_gone(self):
        self.assertEqual(self.rows(TAB, [])[0]["tab"], "main")


R1, R2 = "1" * 32, "2" * 32


def readily_rows(*ids, ok=True):
    """What read_readily_commands returns for these Readily ids."""
    return {"scripts": [{"name": "R" + i[0], "command": "echo " + i[0], "help": "", "id": i,
                         "source": "readily", "readonly": True} for i in ids],
            "skipped": 0, "warning": None if ok else "Readily is not available", "ok": ok}


class ReadilyPlacementTest(CliCase):
    """Readily rows can be moved: their place and tab live in scripts.json as
    slots, their content keeps coming from Obsidian."""

    def setUp(self):
        super().setUp()
        self.answer = readily_rows(R1, R2)
        self.original = engine.read_readily_commands
        engine.read_readily_commands = lambda environ, run: self.answer
        engine.save_config(self.environ, {"readily": {"enabled": True}})

    def tearDown(self):
        engine.read_readily_commands = self.original
        super().tearDown()

    def order(self, payload, tab="main"):
        return [s.get("name") or "—" for s in payload["scripts"] if s["tab"] == tab]

    def slots(self):
        return [s for s in engine.load_library(self.path)["scripts"] if s.get("kind") == "readily"]

    def test_unmoved_rows_come_after_the_scripts(self):
        self.cli(["add"], script("A"))
        self.assertEqual(self.order(self.cli(["list"])[1]), ["A", "R1", "R2"])
        self.assertEqual(self.slots(), [])

    def test_moving_a_readily_row_places_every_row(self):
        self.cli(["add"], script("A"))
        code, payload = self.cli(["move", R1], {"delta": -1})
        self.assertEqual(code, 0)
        self.assertEqual(self.order(payload), ["R1", "A", "R2"])
        self.assertEqual([(s["id"], s["tab"]) for s in self.slots()], [(R1, "main"), (R2, "main")])
        self.assertEqual(self.order(self.cli(["list"])[1]), ["R1", "A", "R2"])

    def test_a_script_can_move_below_readily_rows(self):
        _, payload = self.cli(["add"], script("A"))
        a = payload["scripts"][0]["id"]
        self.cli(["move", a], {"delta": 1})
        _, payload = self.cli(["move", a], {"delta": 1})
        self.assertEqual(self.order(payload), ["R1", "R2", "A"])

    def test_readily_rows_keep_their_content_and_schedule(self):
        engine.save_readily_schedules(self.environ, {R1: {"kind": "interval", "seconds": 60}})
        self.cli(["move", R2], {"delta": -1})
        _, payload = self.cli(["list"])
        row = next(s for s in payload["scripts"] if s["id"] == R1)
        self.assertEqual((row["command"], row["source"], row["schedule"]["seconds"]), ("echo 1", "readily", 60))
        self.assertNotIn("kind", row)

    def test_set_tab_moves_a_readily_row_to_the_end_of_another_tab(self):
        _, payload = self.cli(["tab-add"], {"name": "Obsidian"})
        tab = payload["tabs"][1]["id"]
        self.cli(["add"], script("X", tab=tab))
        code, payload = self.cli(["set-tab", R1], {"tab": tab})
        self.assertEqual(code, 0)
        self.assertEqual(self.order(payload, tab), ["X", "R1"])
        self.assertEqual(self.order(payload), ["R2"])

    def test_set_tab_checks_the_tab(self):
        for tab in ("Bad!", None):
            self.assertEqual(self.cli(["set-tab", R1], {"tab": tab}), (1, {"error": "Invalid tab id"}))
        self.assertEqual(self.cli(["set-tab", R1], {"tab": TAB}), (1, {"error": "No such tab"}))

    def test_set_tab_works_for_scripts_too(self):
        _, payload = self.cli(["tab-add"], {"name": "Obsidian"})
        tab = payload["tabs"][1]["id"]
        _, payload = self.cli(["add"], script("A"))
        a = payload["scripts"][0]["id"]
        _, payload = self.cli(["set-tab", a], {"tab": tab})
        self.assertEqual(self.order(payload, tab), ["A"])

    def test_a_separator_can_go_below_a_readily_row(self):
        code, payload = self.cli(["add"], {"kind": "separator", "label": "x", "after": R1})
        self.assertEqual(code, 0)
        self.assertEqual(self.order(payload), ["R1", "—", "R2"])

    def test_a_row_whose_command_is_gone_is_left_out_then_pruned(self):
        self.cli(["move", R2], {"delta": -1})
        self.answer = readily_rows(R2)
        self.assertEqual(self.order(self.cli(["list"])[1]), ["R2"])
        self.assertEqual(len(self.slots()), 2, "a list only reads")
        self.cli(["move", R2], {"delta": 1})
        self.assertEqual([s["id"] for s in self.slots()], [R2])

    def test_a_failed_read_neither_shows_nor_prunes_slots(self):
        self.cli(["move", R2], {"delta": -1})
        self.answer = readily_rows(ok=False)
        _, payload = self.cli(["list"])
        self.assertEqual(payload["scripts"], [])
        self.assertEqual(self.cli(["move", R2], {"delta": 1}), (1, {"error": "No such script"}))
        self.assertEqual(len(self.slots()), 2)

    def test_slots_are_hidden_while_the_integration_is_off_and_come_back(self):
        self.cli(["move", R2], {"delta": -1})
        engine.save_config(self.environ, {"readily": {"enabled": False}})
        self.assertEqual(self.cli(["list"])[1]["scripts"], [])
        engine.save_config(self.environ, {"readily": {"enabled": True}})
        self.assertEqual(self.order(self.cli(["list"])[1]), ["R2", "R1"])

    def test_hidden_slots_are_not_stepped_over(self):
        _, payload = self.cli(["add"], script("A"))
        a = payload["scripts"][0]["id"]
        self.cli(["move", a], {"delta": 1})          # R1, A, R2
        self.cli(["add"], script("B"))               # R1, A, R2, B
        engine.save_config(self.environ, {"readily": {"enabled": False}})
        _, payload = self.cli(["move", a], {"delta": 1})
        self.assertEqual(self.order(payload), ["B", "A"])

    def test_a_readily_row_cannot_be_edited_here(self):
        self.cli(["move", R2], {"delta": -1})
        code, payload = self.cli(["update", R1], script("X"))
        self.assertEqual((code, payload), (1, {"error": "A Readily command is edited in Obsidian"}))

    def test_a_placed_readily_row_still_runs_its_obsidian_command(self):
        self.cli(["move", R2], {"delta": -1})
        fake = FakeRun([(1, "", "can't find session: x\n"), (0, "", "")])
        out = io.StringIO()
        code = engine.main(["run", R1], environ=self.environ, run=fake, stdin=io.StringIO(""),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("RUNBOOK_COMMAND=echo 1", fake.calls[1])

    def test_slots_do_not_clash_with_script_names(self):
        self.cli(["move", R2], {"delta": -1})
        self.assertEqual(self.cli(["add"], script("R1"))[0], 0)


if __name__ == "__main__":
    unittest.main()
