"""Where the bus keeps its runtime state, and why it is not the temp directory.

iceoryx2 has no daemon. Two processes find each other's services by reading files on
disk, so a directory both ends agree on is REQUIRED. Which directory is not: temp is
iceoryx2's default and nothing more.

TWO LOCATIONS, AND ONLY ONE OF THEM HAS A CONFIGURATION KNOB.

    nodes/ and services/ registry   [global] root-path, in config/iceoryx2.toml
    *.shm_state files               TEMP_DIRECTORY, a compile-time constant

The second is why weftspun/iceoryx2 carries a patch. On Windows SHM_STATE_DIRECTORY
aliases TEMP_DIRECTORY, and neither is reachable from the config file, so the state
files land in the default directory whatever `root-path` says. The fork reads
IOX2_TEMP_DIRECTORY through option_env! at build time; unset, upstream's default stands.

WHY NOT %TEMP%, WHICH IS WHAT WAS TRIED FIRST. It works -- measured, all five
.shm_state files moved there and nothing was written to C:\\Temp. It is still the wrong
home for a service, because machine-wide temp is a shared namespace: a stale iox2_ file
left by a killed encoder sits among every other program's leavings and is nobody's to
reap. A service-owned directory is one this service creates on `up` and removes on
`down`, so a stale run has an owner.

WHY NOT SOMETHING PERSISTENT EITHER. These files must NOT survive a reboot. They name
shared memory segments and live processes, and a registry describing a node that died
with the machine is a stale entry the next start has to clean up. That property -- swept
between boots -- is the one thing temp had going for it, and the runtime directories
below have it too.

SPDX-License-Identifier: Apache-2.0 OR MIT
"""

from __future__ import annotations

import os
import pathlib
import platform
import sys


def runtime_dir() -> pathlib.Path:
    """The directory this service owns. Created by `up`, removed by `down`."""
    override = os.environ.get("CINEFORM_RUNTIME_DIR")
    if override:
        return pathlib.Path(override)

    system = platform.system()
    if system == "Windows":
        # LOCALAPPDATA rather than APPDATA: the roaming profile is copied between
        # machines by domain policy, and a shared-memory registry naming this machine's
        # dead processes is the last thing that should travel.
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise SystemExit(
                "LOCALAPPDATA is unset, so there is no per-user runtime directory to "
                "use. Set CINEFORM_RUNTIME_DIR to choose one explicitly."
            )
        return pathlib.Path(base) / "weftspun" / "cineform" / "run"

    # XDG_RUNTIME_DIR is the specified answer and is already swept between boots. It is
    # absent under su, cron and some containers, which is exactly where a fallback that
    # guessed would put the bus somewhere the other process is not looking -- so the
    # fallback is /run/user/<uid>, the path XDG_RUNTIME_DIR names when it is set at all,
    # and a failure is reported rather than a guess.
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return pathlib.Path(xdg) / "weftspun-cineform"
    uid = os.getuid()  # type: ignore[attr-defined]
    candidate = pathlib.Path("/run/user") / str(uid)
    if candidate.is_dir():
        return candidate / "weftspun-cineform"
    raise SystemExit(
        f"XDG_RUNTIME_DIR is unset and {candidate} does not exist, so there is no "
        "runtime directory to use. Set CINEFORM_RUNTIME_DIR to choose one explicitly."
    )


def iceoryx2_root(run: pathlib.Path) -> pathlib.Path:
    return run / "iceoryx2"


def write_config(run: pathlib.Path) -> pathlib.Path:
    """Writes config/iceoryx2.toml, which iceoryx2 reads from the working directory.

    Priority 1 in iceoryx2's own search order is `config/iceoryx2.toml` RELATIVE TO THE
    PROCESS WORKING DIRECTORY, so every process that joins this bus has to be started
    from the directory holding it. `up` does that; a process started elsewhere silently
    gets the default root path and then cannot see the services, which looks exactly
    like an interactor that is not running.
    """
    config_dir = run / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    root = str(iceoryx2_root(run))
    if not root.endswith(os.sep):
        root += os.sep
    # TOML basic strings take backslash escapes, so a Windows path doubles them. A path
    # written raw parses as an escape sequence and the file is rejected -- and iceoryx2
    # reports a rejected config by falling back to its DEFAULT with a warning, which
    # reads as success until the files turn up somewhere else.
    escaped = root.replace("\\", "\\\\")
    path = config_dir / "iceoryx2.toml"
    path.write_text('[global]\nroot-path = "%s"\n' % escaped, encoding="utf-8")
    return path


def main() -> int:
    run = runtime_dir()
    what = sys.argv[1] if len(sys.argv) > 1 else "run"
    if what == "run":
        print(run)
    elif what == "temp":
        # The value for IOX2_TEMP_DIRECTORY when building the forked iceoryx2. The
        # trailing separator is required; the fork does not append one, because a const
        # fn cannot concatenate byte strings.
        text = str(run)
        print(text if text.endswith(os.sep) else text + os.sep)
    elif what == "root":
        print(iceoryx2_root(run))
    else:
        print(f"unknown query {what!r}; want one of run, temp, root", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
