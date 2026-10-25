#!/usr/bin/env python3
"""Validate the standard ESP build profile without changing the firmware tree."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parents[4] / "lg-g8-sources-20261025/mu_aloha_platforms"
DSC = Path("Platforms/AndromedaPkg/Andromeda.dsc.inc")
LOADER = Path("Platforms/QcomModulePkg/Application/LinuxLoader/LinuxLoader.c")
OPTIONS = Path("Platforms/AndromedaPkg/Library/MsBootOptionsLib/MsBootOptionsLib.c")
MACROS = ("LG_ALPHA_BDS_LINUXLOADER", "LG_ALPHA_FORCE_UEFI_FASTBOOT",
          "LG_ALPHA_FORCE_UEFI_FASTBOOT_EARLY")
EDK_LOGGER_INITIALIZED = False


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def options_block(text: str) -> str:
    return "[BuildOptions]\n" + text.split("[BuildOptions]\n", 1)[1].split("\n[BuildOptions.", 1)[0] + "\n"


def parse_options(source: Path, target: Path, text: str, defines: dict,
                  platform_defines: dict | None = None) -> list[str]:
    # Use this checkout's actual EDK2 DSC parser, including conditional
    # postprocessing. Only the affected BuildOptions block is isolated; this
    # deliberately does not claim that the full platform builds successfully.
    sys.dont_write_bytecode = True
    tools = source / "MU_BASECORE/BaseTools/Source/Python"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    from Common import EdkLogger, GlobalData
    from Common.Misc import PathClass
    from CommonDataClass.DataClass import MODEL_FILE_DSC, MODEL_META_DATA_BUILD_OPTION
    from Workspace.MetaFileParser import DscParser
    from Workspace.MetaFileTable import MetaFileStorage

    global EDK_LOGGER_INITIALIZED
    if not EDK_LOGGER_INITIALIZED:
        EdkLogger.Initialize()
        EDK_LOGGER_INITIALIZED = True
    platform_defines = platform_defines or {}
    target.write_text("""[Defines]
  PLATFORM_NAME = LocalProfileCheck
  PLATFORM_GUID = d3fe7e24-8904-42b8-8073-5d1a562f744c
  PLATFORM_VERSION = 1.0
  DSC_SPECIFICATION = 0x00010005
  OUTPUT_DIRECTORY = Build
  SUPPORTED_ARCHITECTURES = AARCH64
  BUILD_TARGETS = RELEASE
  SKUID_IDENTIFIER = DEFAULT
""" + "".join(f"  {name} = {value}\n" for name, value in platform_defines.items()) + options_block(text))
    GlobalData.gGlobalDefines = {"WORKSPACE": str(target.parent)}
    # Other build-option substitutions are irrelevant to the selector. Give
    # them explicit values rather than relying on an inherited build environment.
    GlobalData.gCommandLineDefines = {
        name: "0" for name in re.findall(r"\$\((\w+)\)", options_block(text))
        if name != "LG_ALPHA_STANDARD_ESP" and name not in platform_defines
    }
    GlobalData.gCommandLineDefines.update(defines)
    meta = PathClass(str(target))
    table = MetaFileStorage(SimpleNamespace(TblFile=[]), meta, MODEL_FILE_DSC, True)
    parser = DscParser(meta, MODEL_FILE_DSC, "AARCH64", table)
    return [record[2] for record in parser[MODEL_META_DATA_BUILD_OPTION, "AARCH64", "COMMON"]]


def preprocess(path: Path, flags: list[str], output: Path,
               extra_macros: tuple[str, ...] = ()) -> str:
    # Include directives alone are omitted. Clang runs the original C
    # preprocessor directives and bodies with the three actual selected defines.
    text = re.sub(r"^[ \t]*#[ \t]*include[^\n]*", "", path.read_text(), flags=re.M)
    selected = [token for line in flags for token in line.split()
                if token.split("=", 1)[0] in {"-D" + name for name in (*MACROS, *extra_macros)}]
    result = subprocess.run(["clang", "-E", "-P", "-x", "c", *selected, "-"],
                            input=text, capture_output=True, text=True, check=True)
    output.write_text(result.stdout)
    return result.stdout


def registrations(text: str) -> list[str]:
    match = re.search(r"MsBootOptionsLibRegisterDefaultBootOptions\s*\(\s*VOID\s*\)\s*\{([^{}]*)\}", text, re.S)
    if not match:
        raise ValueError("Could not locate the actual boot-registration function")
    return re.findall(r"RegisterFvBootOption\s*\([^;]+;", match[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()
    source, out = args.source_root.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.json"
    report_path.unlink(missing_ok=True)
    protected = {str(path): sha((source / path).read_bytes()) for path in (DSC, LOADER, OPTIONS)}
    patch = HERE / "standard-esp.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
    original = (source / DSC).read_text()
    with tempfile.TemporaryDirectory(prefix="scratch-", dir=out) as temp:
        scratch = Path(temp)
        (scratch / DSC).parent.mkdir(parents=True)
        (scratch / DSC).write_text(original)
        subprocess.run(["git", "apply", str(patch)], cwd=scratch, check=True)
        patched = (scratch / DSC).read_text()
    (out / "Andromeda.patched.dsc.inc").write_text(patched)
    before = parse_options(source, out / "original.dsc", original, {})
    diagnostic = parse_options(source, out / "usb-diagnostic.dsc", patched, {})
    standard = parse_options(source, out / "standard-esp.dsc", patched, {"LG_ALPHA_STANDARD_ESP": "TRUE"})
    assert before == diagnostic, "default compiler flags changed"
    for name in MACROS:
        assert any("-D" + name in line.split() for line in diagnostic), name
        assert not any("-D" + name in line.split() for line in standard), name
    standard_without_macros = [" ".join(token for token in line.split() if token not in {"-D" + m for m in MACROS})
                               for line in diagnostic]
    assert standard_without_macros == [" ".join(line.split()) for line in standard]
    rejected = False
    print("Expecting EDK2 to reject the deliberately invalid FALSE profile:", flush=True)
    try:
        parse_options(source, out / "invalid-false.dsc", patched, {"LG_ALPHA_STANDARD_ESP": "FALSE"})
    except Exception as error:
        from Common.BuildToolError import ERROR_STATEMENT, FatalError
        if not isinstance(error, FatalError) or error.args != (ERROR_STATEMENT,):
            raise
        rejected = True
    assert rejected, "FALSE must not silently select an unintended profile"
    cpp = {}
    for profile, flags in (("original", before), ("usb-diagnostic", diagnostic), ("standard-esp", standard)):
        cpp[profile] = {
            "loader": preprocess(source / LOADER, flags, out / f"{profile}-loader.i"),
            "options": preprocess(source / OPTIONS, flags, out / f"{profile}-options.i"),
        }
    assert cpp["original"] == cpp["usb-diagnostic"], "default C preprocessing changed"
    assert "LG alpha fastboot after stack setup" in cpp["usb-diagnostic"]["loader"]
    assert "LG alpha fastboot after stack setup" not in cpp["standard-esp"]["loader"]
    default_calls = registrations(cpp["usb-diagnostic"]["options"])
    standard_calls = registrations(cpp["standard-esp"]["options"])
    assert "mLinuxLoaderFileGuid" in default_calls[0]
    assert all("mLinuxLoaderFileGuid" not in call for call in standard_calls)
    assert default_calls[1:] == standard_calls
    assert 'L"Internal Storage"' in standard_calls[0] and '"SDD"' in standard_calls[0]
    assert 'L"Internal UEFI Shell 2.0"' in standard_calls[-1]
    # The upstream tree already has a NoSb platform target. Verify its actual
    # defines through the same DSC parser and its default-key FDF condition
    # through the same expression evaluator used by GenFds.
    target_defines = {}
    target_flags = {}
    target_hashes = {}
    for name in ("SurfaceDuo1", "SurfaceDuo1NoSb"):
        path = source / f"Platforms/SurfaceDuo1Pkg/{name}.dsc"
        data = path.read_text()
        target_defines[name] = {macro: re.search(rf"(?m)^\s*{macro}\s*=\s*(\S+)", data).group(1)
                                for macro in ("SECURE_BOOT", "DEFAULT_KEYS")}
        target_hashes[name] = sha(path.read_bytes())
        target_flags[name] = parse_options(source, out / f"{name}-standard-esp.dsc", patched,
            {"LG_ALPHA_STANDARD_ESP": "TRUE"}, target_defines[name])
    assert target_defines["SurfaceDuo1"] == {"SECURE_BOOT": "1", "DEFAULT_KEYS": "TRUE"}
    assert target_defines["SurfaceDuo1NoSb"] == {"SECURE_BOOT": "0", "DEFAULT_KEYS": "FALSE"}
    provision = source / "Platforms/AndromedaPkg/Driver/SecureBootProvisioningDxe/SecureBootProvisioningDxe.c"
    normal_provision = preprocess(provision, target_flags["SurfaceDuo1"], out / "sb-provision.i", ("SECURE_BOOT",))
    dev_provision = preprocess(provision, target_flags["SurfaceDuo1NoSb"], out / "nosb-provision.i", ("SECURE_BOOT",))
    assert "EFI_PLATFORM_KEY_NAME" not in normal_provision
    assert "EFI_PLATFORM_KEY_NAME" in dev_provision and "PK exists.  Secure boot on." in dev_provision
    keys = source / "MU_BASECORE/ArmPlatformPkg/SecureBootDefaultKeys.fdf.inc"
    guard = re.search(r"(?m)^!if\s+(.+DEFAULT_KEYS.+)$", keys.read_text()).group(1)
    from Common.Expression import ValueExpression
    key_conditions = {name: bool(ValueExpression(guard, values)()) for name, values in target_defines.items()}
    assert key_conditions == {"SurfaceDuo1": True, "SurfaceDuo1NoSb": False}
    after = {str(path): sha((source / path).read_bytes()) for path in (DSC, LOADER, OPTIONS)}
    assert protected == after, "external firmware source changed during validation"
    report = {"passed": True, "scope": "EDK2 conditional parsing and C preprocessing only; no firmware execution or phone access",
              "source_root": str(source), "source_hashes": protected, "patch_sha256": sha(patch.read_bytes()),
              "validator_sha256": sha(Path(__file__).read_bytes()),
              "edk2_dsc_parser_sha256": sha((source / "MU_BASECORE/BaseTools/Source/Python/Workspace/MetaFileParser.py").read_bytes()),
              "default_flags_unchanged": True, "default_preprocessed_C_unchanged": True,
              "standard_profile_experimental_macros": [], "invalid_false_rejected": rejected,
              "external_source_unchanged": True,
              "default_boot_registration": default_calls, "standard_boot_registration": standard_calls,
              "development_target": {"defines": target_defines, "target_dsc_sha256": target_hashes,
                  "default_key_FDF_condition": guard, "default_key_FDF_enabled": key_conditions,
                  "nosb_still_checks_existing_PK": True,
                  "scope": "source/parser/preprocessor proof; runtime SecureBoot state was not inspected"}}
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: patch applies to a scratch copy; external source unchanged")
    print("PASS: actual EDK2 parser keeps default USB diagnostics unchanged")
    print("PASS: explicit STANDARD_ESP removes all three experimental macros; FALSE rejected")
    print("PASS: actual C preprocessing retains disk/USB/PXE/menu/Shell registrations, omits first LinuxLoader")
    print("PASS: existing NoSb target selects SECURE_BOOT=0 and DEFAULT_KEYS=FALSE; existing PK remains relevant")
    print("Report:", report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
