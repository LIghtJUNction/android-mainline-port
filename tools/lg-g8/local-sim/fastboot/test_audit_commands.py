"""Synthetic tables only: no private firmware or device output."""
import unittest
import subprocess
from audit_commands import audit


def table(entries):
    return 'struct FastbootCmdDesc cmd_list[] = {{"", NULL},' + entries + '};'


class CommandAuditTests(unittest.TestCase):
    def test_effective_configuration(self):
        source = table('''
{"boot", CmdBoot},
#ifdef ENABLE_UPDATE_PARTITIONS_CMDS
{"flash:", CmdFlash},
#endif
#ifdef ENABLE_DEVICE_CRITICAL_LOCK_UNLOCK_CMDS
{"flashing unlock_critical", CmdFlashingUnLockCritical},
#endif
''')
        for flags in [[], ["-DENABLE_UPDATE_PARTITIONS_CMDS"],
                      ["-DENABLE_DEVICE_CRITICAL_LOCK_UNLOCK_CMDS"],
                      ["-DENABLE_UPDATE_PARTITIONS_CMDS", "-DENABLE_DEVICE_CRITICAL_LOCK_UNLOCK_CMDS"]]:
            with self.subTest(flags=flags):
                output = subprocess.run(["cc", "-E", "-P", "-x", "c", *flags, "-"],
                                        input=source, capture_output=True, text=True, check=True).stdout
                if flags:
                    with self.assertRaises(ValueError):
                        audit(output)
                else:
                    self.assertEqual(audit(output), [{"name": "boot", "handler": "CmdBoot"}])

    def test_ram_surface(self):
        self.assertEqual(len(audit(table('{"getvar:", CmdGetVar}, {"download:", CmdDownload}, {"boot", CmdBoot},'))), 3)

    def test_dangerous_and_unknown_commands(self):
        for name in ["flash:", "erase:", "set_active", "flashing unlock", "flashing lock",
                     "flashing unlock_critical", "flashing lock_critical", "snapshot-update",
                     "oem enable-charger-screen", "reboot", "future-command"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit(table('{"' + name + '", CmdUnknown},'))

    def test_wrong_handler(self):
        with self.assertRaises(ValueError):
            audit(table('{"boot", CmdFlash},'))

    def test_unprocessed_configuration(self):
        with self.assertRaises(ValueError):
            audit(table('\n#ifdef ENABLE_UPDATE_PARTITIONS_CMDS\n{"flash:", CmdFlash},\n#endif\n'))

    def test_malformed_or_ambiguous_input(self):
        good = table('{"boot", CmdBoot},')
        for text in ["", table(""), good + good, good + "execute();",
                     table('{"boot", CmdBoot}, {"boot", CmdBoot},'),
                     table('{"boot", CmdBoot} {"getvar:", CmdGetVar},'),
                     table('{COMMAND, CmdBoot},'), table('{"bo\\x6ft", CmdBoot},'),
                     good.replace('{"", NULL},', ''), table('{"", NULL},')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                audit(text)

    def test_comments(self):
        self.assertEqual(audit(table('/* build note */ {"boot", CmdBoot}, // note\n')),
                         [{"name": "boot", "handler": "CmdBoot"}])


if __name__ == "__main__":
    unittest.main()
