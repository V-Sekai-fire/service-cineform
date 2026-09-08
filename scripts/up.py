"""Stand the bus up, start the encoder, and leave the terminal free for the display.

WHAT A 7-SERVICE IS FOR, stated because the alternative keeps suggesting itself. The
encoder and the display are two processes that must agree on a shared-memory namespace,
a library path and a runtime directory. Every one of those is a property of the
DEPLOYMENT rather than of either program, so putting them in either one would make that
program carry a second job. The first version of this work was about to grow an offline
`--encode` flag on the interactor so a test could skip the bus; that flag would have
been a second transport on a component whose whole definition is that it has one.

SPDX-License-Identifier: Apache-2.0 OR MIT
"""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from runtime_dir import iceoryx2_root, runtime_dir, write_config  # noqa: E402


def ffi_library_name() -> str:
    system = platform.system()
    if system == "Windows":
        return "iceoryx2_ffi_c.dll"
    if system == "Darwin":
        return "libiceoryx2_ffi_c.dylib"
    return "libiceoryx2_ffi_c.so"


def libclang_dir() -> pathlib.Path | None:
    """The clang shared library beside the interpreter running this script, if any."""
    prefix = pathlib.Path(sys.prefix)
    for candidate in (prefix / "Library" / "bin", prefix / "lib", prefix / "bin"):
        for name in ("libclang.dll", "libclang.so", "libclang.dylib", "clang.dll"):
            if (candidate / name).is_file():
                return candidate
    return None


def build_iceoryx2(run: pathlib.Path, iceoryx2_dir: pathlib.Path) -> pathlib.Path:
    """Builds the FFI shared library, with the runtime directory compiled into it.

    IOX2_TEMP_DIRECTORY is read by weftspun/iceoryx2's fork at COMPILE time, because the
    constant it feeds builds `Path` values in const contexts and cannot be a runtime
    lookup without changing those call sites. That makes the library specific to this
    runtime directory, which is why it is built here rather than installed from a
    package.
    """
    if not (iceoryx2_dir / "Cargo.toml").is_file():
        raise SystemExit(
            f"no iceoryx2 checkout at {iceoryx2_dir}.\n"
            "  It is a manifest project. From the repository root:\n"
            "    repo init -u https://github.com/weftspun/service-cineform -m default.xml\n"
            "    repo sync"
        )
    temp_value = str(run)
    if not temp_value.endswith(os.sep):
        temp_value += os.sep

    env = dict(os.environ)
    env["IOX2_TEMP_DIRECTORY"] = temp_value
    # iceoryx2's posix layer generates its bindings with bindgen, which loads a
    # clang shared library and names LIBCLANG_PATH when it finds none. The
    # environment that runs this script installs one, so point at that rather
    # than asking a caller to remember.
    if "LIBCLANG_PATH" not in env:
        found = libclang_dir()
        if found is not None:
            env["LIBCLANG_PATH"] = str(found)
            print(f"using LIBCLANG_PATH={found}")
    print(f"building iceoryx2-ffi-c with IOX2_TEMP_DIRECTORY={temp_value}")
    subprocess.run(
        ["cargo", "build", "--release", "-p", "iceoryx2-ffi-c"],
        cwd=iceoryx2_dir, env=env, check=True,
    )
    lib = iceoryx2_dir / "target" / "release" / ffi_library_name()
    if not lib.is_file():
        raise SystemExit(f"cargo reported success but {lib} is absent")
    return lib


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interactor", type=pathlib.Path,
                    default=ROOT / "interactor-cineform" / "build" / "interactor-cineform",
                    help="the encoder binary")
    ap.add_argument("--iceoryx2", type=pathlib.Path, default=ROOT / "thirdparty" / "iceoryx2")
    ap.add_argument("--skip-build", action="store_true",
                    help="use an already built FFI library rather than running cargo")
    ap.add_argument("--print-env", action="store_true",
                    help="print the environment and the working directory, and start nothing")
    args = ap.parse_args()

    run = runtime_dir()
    root = iceoryx2_root(run)
    # services/ and nodes/ are created rather than left to iceoryx2, which does not make
    # them and fails at node creation without naming the path it wanted -- the whole
    # diagnostic is "no node".
    (root / "services").mkdir(parents=True, exist_ok=True)
    (root / "nodes").mkdir(parents=True, exist_ok=True)
    config = write_config(run)
    print(f"runtime directory  {run}")
    print(f"iceoryx2 root      {root}")
    print(f"config             {config}")

    lib = None
    if args.skip_build:
        env_lib = os.environ.get("WEFT_ICEORYX2_PATH")
        if env_lib and pathlib.Path(env_lib).is_file():
            lib = pathlib.Path(env_lib)
        else:
            candidate = args.iceoryx2 / "target" / "release" / ffi_library_name()
            if candidate.is_file():
                lib = candidate
        if lib is None:
            raise SystemExit(
                "--skip-build was given but no FFI library was found. Set "
                "WEFT_ICEORYX2_PATH, or drop --skip-build to build it."
            )
    else:
        lib = build_iceoryx2(run, args.iceoryx2)
    print(f"iceoryx2 library   {lib}")

    if args.print_env:
        # Everything a second shell needs to join this bus. The working directory is part
        # of it: iceoryx2 reads config/iceoryx2.toml relative to the PROCESS working
        # directory, so a process started elsewhere silently gets the default root path
        # and then cannot see the services -- which looks exactly like a missing encoder.
        print("\n# join this bus with:")
        print(f'cd "{run}"')
        print(f'export WEFT_ICEORYX2_PATH="{lib}"')
        return 0

    interactor = args.interactor
    if not interactor.is_file():
        for suffix in (".exe",):
            if interactor.with_suffix(suffix).is_file():
                interactor = interactor.with_suffix(suffix)
                break
    if not interactor.is_file():
        raise SystemExit(
            f"no encoder binary at {args.interactor}.\n"
            "  Build interactor-cineform first, or pass --interactor."
        )

    env = dict(os.environ)
    env["WEFT_ICEORYX2_PATH"] = str(lib)
    print(f"\nstarting {interactor.name} in {run}")
    print("stop it with ctrl-c\n")
    # cwd is the runtime directory, so the config file above is the one it reads.
    return subprocess.run([str(interactor)], cwd=run, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
