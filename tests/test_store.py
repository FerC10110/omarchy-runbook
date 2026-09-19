import json
import os
import stat
import tempfile
import unittest

from _load import load

engine = load()

FIELDS = {"name": "Ports (all)", "command": "sudo /home/fer/.local/bin/ports -a",
          "help": "Lists every listening socket."}


class LibraryPathTest(unittest.TestCase):
    def test_uses_xdg_config_home_when_set(self):
        path = engine.library_path({"XDG_CONFIG_HOME": "/x/cfg", "HOME": "/h"})
        self.assertEqual(path, "/x/cfg/runbook/scripts.json")

    def test_falls_back_to_home_dot_config(self):
        path = engine.library_path({"HOME": "/h"})
        self.assertEqual(path, "/h/.config/runbook/scripts.json")


class LoadSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "runbook", "scripts.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_an_empty_library(self):
        self.assertEqual(engine.load_library(self.path),
                         {"version": 1, "view": {"width": 960, "height": 540},
                          "tabs": [{"id": "main", "name": "Mine"}], "scripts": []})

    def test_invalid_json_is_an_engine_error(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), "scripts.json is not valid JSON")

    def test_save_creates_private_dir_and_file(self):
        engine.save_library(self.path, engine.empty_library())
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode)
        file_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)
        with open(self.path) as fh:
            self.assertEqual(json.load(fh)["version"], 1)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["scripts.json"])

    def test_save_then_load_round_trips(self):
        library = engine.empty_library()
        script = engine.add_script(library, FIELDS)
        engine.save_library(self.path, library)
        loaded = engine.load_library(self.path)
        self.assertEqual(loaded["scripts"], [script])

    def test_load_repairs_bad_view_when_scripts_are_valid(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"version": 1, "view": {"width": 10, "height": "x"},
                       "scripts": [{"id": "a" * 32, "name": "ok", "command": "true", "help": ""}]}, fh)
        loaded = engine.load_library(self.path)
        self.assertEqual(loaded["view"], {"width": 960, "height": 540})
        self.assertEqual([s["id"] for s in loaded["scripts"]], ["a" * 32])

    def test_load_raises_on_a_broken_script_instead_of_dropping_it(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"version": 1, "view": {"width": 10, "height": "x"},
                       "scripts": [{"id": "nope", "name": "a", "command": "b", "help": ""},
                                   {"id": "a" * 32, "name": "ok", "command": "true", "help": ""}]}, fh)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), 'scripts.json: script 1 ("a") has an invalid id')

    def test_load_raises_naming_the_entry_on_an_invalid_field(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"version": 1,
                       "scripts": [{"id": "a" * 32, "name": "ok", "command": "true", "help": ""},
                                   {"id": "b" * 32, "name": "", "command": "true", "help": ""}]}, fh)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), 'scripts.json: script 2 (""): Name must be 1-64 characters')

    def test_load_raises_scripts_must_be_a_list(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"version": 1, "scripts": "nope"}, fh)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), "scripts.json: scripts must be a list")

    def test_load_raises_when_a_script_is_not_an_object(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"version": 1, "scripts": [{"id": "a" * 32, "name": "ok", "command": "true"}, "nope"]}, fh)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), "scripts.json: script 2 is not an object")

    def test_load_raises_on_duplicate_id(self):
        os.makedirs(os.path.dirname(self.path))
        dup = "a" * 32
        with open(self.path, "w") as fh:
            json.dump({"version": 1, "scripts": [{"id": dup, "name": "one", "command": "true"},
                                                  {"id": dup, "name": "two", "command": "true"}]}, fh)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.load_library(self.path)
        self.assertEqual(str(ctx.exception), "scripts.json: duplicate id " + dup)


class ValidationTest(unittest.TestCase):
    def test_valid_fields_are_normalised(self):
        self.assertEqual(engine.validate_script({"name": "  Ports ", "command": "ls\n", "help": "x"}),
                         {"name": "Ports", "command": "ls", "help": "x"})

    def test_help_is_optional(self):
        self.assertEqual(engine.validate_script({"name": "a", "command": "b"})["help"], "")

    def test_rejects_empty_name(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.validate_script({"name": "  ", "command": "ls"})
        self.assertEqual(str(ctx.exception), "Name must be 1-64 characters")

    def test_rejects_control_characters_in_name(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.validate_script({"name": "a\tb", "command": "ls"})
        self.assertEqual(str(ctx.exception), "Name must not contain control characters")

    def test_rejects_long_name(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_script({"name": "n" * 65, "command": "ls"})

    def test_rejects_empty_command(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.validate_script({"name": "a", "command": ""})
        self.assertEqual(str(ctx.exception), "Command must be 1-4096 characters")

    def test_rejects_long_help(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.validate_script({"name": "a", "command": "ls", "help": "h" * 4097})
        self.assertEqual(str(ctx.exception), "Help must be at most 4096 characters")

    def test_rejects_non_string_fields(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_script({"name": 3, "command": "ls"})

    def test_view_bounds(self):
        self.assertEqual(engine.validate_view({"width": 700, "height": 400.0}), {"width": 700, "height": 400})
        for bad in ({"width": 599, "height": 400}, {"width": 4001, "height": 400},
                    {"width": 700, "height": 359}, {"width": 700, "height": 3001}, {"width": "a", "height": 400}):
            with self.assertRaises(engine.RunbookError, msg=bad):
                engine.validate_view(bad)


class ValidateScheduleTest(unittest.TestCase):
    def test_none_is_unscheduled(self):
        self.assertIsNone(engine.validate_schedule(None))

    def test_interval_ok(self):
        self.assertEqual(engine.validate_schedule({"kind": "interval", "seconds": 1800}),
                         {"kind": "interval", "seconds": 1800})

    def test_interval_bounds(self):
        for bad in (59, 604801):
            with self.assertRaises(engine.RunbookError):
                engine.validate_schedule({"kind": "interval", "seconds": bad})

    def test_interval_seconds_must_be_int(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_schedule({"kind": "interval", "seconds": "600"})

    def test_calendar_shape_ok(self):
        self.assertEqual(engine.validate_schedule({"kind": "calendar", "oncalendar": "*-*-* 08:00:00"}),
                         {"kind": "calendar", "oncalendar": "*-*-* 08:00:00"})
        self.assertEqual(engine.validate_schedule({"kind": "calendar", "oncalendar": "Mon *-*-* 08:00:00"})["oncalendar"],
                         "Mon *-*-* 08:00:00")

    def test_calendar_trailing_newline_rejected(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.validate_schedule({"kind": "calendar", "oncalendar": "*-*-* 08:00:00\n"})
        self.assertEqual(str(ctx.exception), "Not a valid schedule")
        self.assertEqual(engine.validate_schedule({"kind": "calendar", "oncalendar": "*-*-* 08:00:00"}),
                         {"kind": "calendar", "oncalendar": "*-*-* 08:00:00"})

    def test_calendar_weekday_set_ok(self):
        for expr in ("Mon,Wed,Fri *-*-* 08:00:00", "Mon..Wed *-*-* 08:00:00",
                     "Mon..Wed,Fri *-*-* 08:00:00", "Sat,Sun *-*-* 08:00:00"):
            self.assertEqual(engine.validate_schedule({"kind": "calendar", "oncalendar": expr}),
                             {"kind": "calendar", "oncalendar": expr}, msg=expr)

    def test_calendar_weekday_set_bad(self):
        for expr in ("Mon,Xyz *-*-* 08:00:00", "Mon, *-*-* 08:00:00", "Mon.. *-*-* 08:00:00",
                     "Mon,,Wed *-*-* 08:00:00", "foo *-*-* 08:00:00"):
            with self.assertRaises(engine.RunbookError, msg=expr):
                engine.validate_schedule({"kind": "calendar", "oncalendar": expr})

    def test_calendar_bad_shape(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_schedule({"kind": "calendar", "oncalendar": "todos los dias"})

    def test_unknown_kind(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_schedule({"kind": "cron", "expr": "* * * * *"})

    def test_not_an_object(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_schedule("daily")


class ScheduleInScriptTest(unittest.TestCase):
    def test_validate_script_keeps_schedule_when_present(self):
        out = engine.validate_script({"name": "a", "command": "b", "help": "",
                                      "schedule": {"kind": "interval", "seconds": 600}})
        self.assertEqual(out["schedule"], {"kind": "interval", "seconds": 600})

    def test_validate_script_without_schedule_key_has_none(self):
        out = engine.validate_script({"name": "a", "command": "b", "help": ""})
        self.assertNotIn("schedule", out)

    def test_normalise_rejects_bad_schedule_naming_entry(self):
        raw = {"version": 1, "view": {"width": 960, "height": 540},
               "scripts": [{"id": "a" * 32, "name": "ok", "command": "true", "help": "",
                            "schedule": {"kind": "cron"}}]}
        with self.assertRaises(engine.RunbookError) as cm:
            engine._normalise(raw)
        self.assertIn("ok", str(cm.exception))


class CrudTest(unittest.TestCase):
    def setUp(self):
        self.library = engine.empty_library()

    def test_add_appends_with_generated_hex_id(self):
        script = engine.add_script(self.library, FIELDS)
        self.assertRegex(script["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.library["scripts"], [script])
        self.assertEqual(script["name"], "Ports (all)")

    def test_add_rejects_duplicate_name_case_insensitively(self):
        engine.add_script(self.library, FIELDS)
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.add_script(self.library, dict(FIELDS, name="ports (ALL)"))
        self.assertEqual(str(ctx.exception), "A script named \"ports (ALL)\" already exists")

    def test_update_keeps_id_and_position(self):
        first = engine.add_script(self.library, FIELDS)
        engine.add_script(self.library, dict(FIELDS, name="Second"))
        updated = engine.update_script(self.library, first["id"], dict(FIELDS, help="new"))
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(self.library["scripts"][0]["help"], "new")

    def test_update_allows_keeping_own_name(self):
        first = engine.add_script(self.library, FIELDS)
        engine.update_script(self.library, first["id"], dict(FIELDS, name="PORTS (ALL)"))
        self.assertEqual(self.library["scripts"][0]["name"], "PORTS (ALL)")

    def test_update_unknown_id_fails(self):
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.update_script(self.library, "f" * 32, FIELDS)
        self.assertEqual(str(ctx.exception), "No such script")

    def test_remove_returns_removed_script(self):
        script = engine.add_script(self.library, FIELDS)
        self.assertEqual(engine.remove_script(self.library, script["id"]), script)
        self.assertEqual(self.library["scripts"], [])

    def test_set_view_validates(self):
        self.assertEqual(engine.set_view(self.library, {"width": 1200, "height": 700}),
                         {"width": 1200, "height": 700})
        self.assertEqual(self.library["view"], {"width": 1200, "height": 700})
