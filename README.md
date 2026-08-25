# service-cineform

The CineForm encode service: the iceoryx2 bus and `interactor-cineform`, stood up together
on one host, in a runtime directory this service owns.

    python scripts/up.py

## Where the dependencies come from

    repo sync                       # from the workspace root
    python scripts/up.py

The four things this service composes are in the workspace's own manifest, at
`thirdparty/iceoryx2`, `thirdparty/cineform-sdk`, `thirdparty/libwebm` and
`thirdparty/ftxui` under this directory. `default.xml` here lists the same set and is the
root for a deployment-only checkout that has no workspace around it.

Both are needed and neither replaces the other. A manifest root's own list is NOT read when
the project is checked out as one project among ninety-eight, so a workspace sync used to
give the three programs and nothing they link, and building either one meant a second,
private checkout of contract-bus. Two copies of that is the drift `wire.hpp` exists to
prevent, since it defines the four things iceoryx2 compares at connect time.

**Do not run `repo init` inside this directory.** It walks up, finds the workspace client
and re-points the goal manifest at this project, reporting only that it initialised
somewhere else. Build against the checkouts that are already there:

    # the encoder, from 3-interactor/interactor-cineform
    cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5       -DHARNESS_DIR=../../2-contract/bus       -DCINEFORM_DIR=../../7-service/service-cineform/thirdparty/cineform-sdk       -DLIBWEBM_DIR=../../7-service/service-cineform/thirdparty/libwebm

    # the display, from 1-transport/transport-cineform-tui
    cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release       -DHARNESS_DIR=../../2-contract/bus       -DINTERACTOR_DIR=../../3-interactor/interactor-cineform       -DFTXUI_DIR=../../7-service/service-cineform/thirdparty/ftxui

Measured on Windows 11 with clang 22.1.8, CMake 4.4.2 and Ninja 1.13.2: the encoder is 95
targets and the display is 71, and both link against one contract-bus checkout.

## Why a service side at all

`interactor-cineform` encodes and `transport-cineform-tui` displays. Neither of them owns
the things that make the pair *run*: a shared-memory namespace, a library path, and a
directory on disk both processes agree on. Every one of those is a property of the
deployment rather than of either program.

That distinction was nearly lost. The first attempt at a round-trip test was about to add
an offline `--encode` flag to the interactor so the test could skip the bus — a second
transport, on a component whose whole definition is that it has one. The test belongs here
instead, running against the real thing, and `proof/roundtrip.py` is it.

## Where the bus keeps its state, and why it is not temp

iceoryx2 has **no daemon**. Two processes find each other's services by reading files on
disk, so a directory both ends agree on is required. *Which* directory is not — temp is
iceoryx2's default and nothing more.

There are two locations and only one has a configuration knob:

| what                          | set by                                  | knob? |
| ----------------------------- | --------------------------------------- | ----- |
| `nodes/` + `services/` registry | `[global] root-path` in `config/iceoryx2.toml` | yes |
| `*.shm_state` files           | `TEMP_DIRECTORY`, a compile-time constant | **no** |

The second is why `weftspun/iceoryx2` carries a patch. On Windows `SHM_STATE_DIRECTORY`
aliases `TEMP_DIRECTORY` and neither is reachable from the config file, so the state files
land in the default directory whatever `root-path` says. The fork reads
`IOX2_TEMP_DIRECTORY` through `option_env!` at build time; unset, upstream's default
stands. `scripts/up.py` sets it, which is why this service builds the FFI library rather
than installing one.

**The default is `C:\Temp\`, which Windows does not define.** Nothing creates it, so on a
machine where it is absent the first node creation fails — and it fails without naming the
path. The entire diagnostic is:

    publisher: no node

preceded by two warnings about `/etc/passwd` and a missing config file, neither of which is
the cause. `mkdir C:\Temp` is the fix and is not discoverable from that output.

**`%TEMP%` was tried and works, and is still not right.** Measured: all five `.shm_state`
files moved there and nothing was written to `C:\Temp`. But machine-wide temp is a shared
namespace, so a stale `iox2_` file left by a killed encoder sits among every other
program's leavings and is nobody's to reap.

So the runtime directory is one this service owns:

| platform | directory                                                  |
| -------- | ---------------------------------------------------------- |
| Windows  | `%LOCALAPPDATA%\weftspun\cineform\run\`                      |
| Linux    | `$XDG_RUNTIME_DIR/weftspun-cineform/`, else `/run/user/<uid>/…` |

`LOCALAPPDATA` rather than `APPDATA`: the roaming profile is copied between machines by
domain policy, and a registry naming this machine's dead processes is the last thing that
should travel. Override with `CINEFORM_RUNTIME_DIR`.

**These files must not survive a reboot.** They name shared memory and live processes, so a
registry describing a node that died with the machine is a stale entry the next start has
to clean. Being swept between boots is the one property temp had going for it, and both
directories above have it too.

**Every process must start from the runtime directory.** iceoryx2 reads
`config/iceoryx2.toml` relative to the *process working directory*. A process started
elsewhere silently gets the default root path and cannot see the services — which looks
exactly like an encoder that is not running. `scripts/up.py --print-env` prints the `cd`
and the export a second shell needs.

## Bring it up

    repo init -u https://github.com/weftspun/service-cineform -m default.xml
    repo sync

    # build the two halves first
    cmake -S interactor-cineform -B interactor-cineform/build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    cmake --build interactor-cineform/build --parallel
    cmake -S transport-cineform-tui -B transport-cineform-tui/build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release
    cmake --build transport-cineform-tui/build --parallel

    python scripts/up.py

Then, in the shell `--print-env` describes:

    cineform-tui -testsrc -s 640x360 -r 30 -frames 60 out.mkv

## The round trip

`proof/roundtrip.py` takes uncompressed frames, encodes them to the intermediate through
the real bus, decodes them back with FFmpeg, and compares sample by sample.

    python proof/roundtrip.py \
      --source take.mkv --tui transport-cineform-tui/build/cineform-tui \
      --work /tmp/rt --frames 6 --size 1920x1080

**The measurement is the pixels, not the exit code.** An encoder that writes a file FFmpeg
can open has proved the container is well formed and nothing about the image.

Measured on Windows 11, clang 22.1.8, FFmpeg 8.1.2, iceoryx2 v0.9.3, six frames of a
1920×1080 CineForm `gbrap12le` source at quality 2:

| channel | RGB_444          | RGBA_4444        |
| ------- | ---------------- | ---------------- |
| R       | max 9, 53.84 dB  | max 9, 53.84 dB  |
| G       | max 5, 57.42 dB  | max 5, 57.42 dB  |
| B       | max 8, 53.84 dB  | max 8, 53.84 dB  |
| A       | not carried      | max 9, 67.01 dB  |
| ratio   | 11.25:1          | 9.63:1           |

Both runs pass, including their negative control.

**There is no PSNR threshold here,** deliberately. CineForm is lossy and this reports how
lossy; a threshold nobody derived is a number that becomes a fact. The hard assertions are
the frame count and the negative control.

**The negative control caught a defect in itself.** The first version measured
`max()` across all four channels. Without `-alpha` the encoder is told `RGB_444` and does
not carry alpha at all, so A already read a max error of 255 — the largest a byte can
differ by — and no corruption could exceed it. The gate reported that it proved nothing,
which was correct. It now measures only the channels that were actually encoded.

## Licence

`Apache-2.0 OR MIT`.
