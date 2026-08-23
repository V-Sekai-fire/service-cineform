"""Uncompressed in, CineForm out, uncompressed back, and what the codec cost.

THE MEASUREMENT IS THE PIXELS, NOT THE EXIT CODE. An encoder that writes a file ffmpeg
can open has proved that the container is well formed and nothing at all about the
image. So this decodes the intermediate back to the same uncompressed format the source
was in and compares them sample by sample.

WHAT RUNS. Everything, through the real bus: the TUI publishes a job over iceoryx2, the
interactor encodes it, and the reply comes back the same way. There is no offline path
that skips the transport, deliberately -- standing the bus up is what the 7-service side
is for, and a codec test that bypasses it would be measuring a different program from
the one that gets deployed.

THE NEGATIVE CONTROL IS NOT OPTIONAL. A comparison that only ever passes certifies a
broken encoder as readily as a working one, so the run also corrupts the intermediate
and asserts that the same comparison FAILS on it. If both pass, the gate is decoration
and the run reports that rather than a green tick.

WHAT THIS DOES NOT MEASURE. CineForm is lossy and this reports how lossy, not whether
that is acceptable -- there is no pass threshold on PSNR here, because a threshold
nobody derived is a number that turns into a fact. The one hard assertion is the
negative control and the frame count.

SPDX-License-Identifier: Apache-2.0 OR MIT
"""

from __future__ import annotations

import argparse
import math
import os
import pathlib
import shutil
import struct
import subprocess
import sys

# Household equivalents for the sizes this prints, because CLAUDE.md asks a measurement
# to come with something a reader can picture. A byte count is not a length, so the
# anchor here is time and stack height rather than a coin.
CREDIT_CARD_MM = 0.76


def run(cmd, **kw):
    """Runs a command and fails loudly. A silent skip reads exactly like a pass."""
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        sys.stderr.write(f"FAILED: {' '.join(str(c) for c in cmd)}\n")
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(1)
    return proc


def probe(path: pathlib.Path, entries: str) -> str:
    return run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]).stdout.strip()


def extract_uncompressed(source: pathlib.Path, dest: pathlib.Path, frames: int,
                         width: int, height: int) -> None:
    """Source video -> packed 8-bit RGBA, which is what the interactor reads.

    rgba, not rgb24: the encoder is fed 4 bytes per pixel and told the size, so a
    3-byte-per-pixel file would be read as a shorter, sheared image rather than refused.
    """
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-frames:v", str(frames),
        "-vf", f"scale={width}:{height}",
        "-pix_fmt", "rgba", "-f", "rawvideo", str(dest),
    ])


def decode_to_uncompressed(source: pathlib.Path, dest: pathlib.Path) -> None:
    run([
        "ffmpeg", "-v", "error", "-y", "-i", str(source),
        "-pix_fmt", "rgba", "-f", "rawvideo", str(dest),
    ])


def compare(a: pathlib.Path, b: pathlib.Path, width: int, height: int, frames: int):
    """Per-channel max error and PSNR, over the frames both files share.

    Alpha is reported separately rather than folded into the average. The encoder is
    told RGB_444 or RGBA_4444, and in the RGB case alpha is not carried at all -- an
    average across four channels would hide a dropped alpha channel behind three good
    ones.
    """
    frame_bytes = width * height * 4
    have_a = a.stat().st_size // frame_bytes
    have_b = b.stat().st_size // frame_bytes
    common = min(have_a, have_b, frames)
    if common == 0:
        raise SystemExit(f"FAIL nothing to compare: {have_a} and {have_b} whole frames")

    # Sum of squared error per channel, accumulated in Python ints so a long run cannot
    # overflow silently the way a 32-bit accumulator would.
    sse = [0, 0, 0, 0]
    peak = [0, 0, 0, 0]
    with a.open("rb") as fa, b.open("rb") as fb:
        for _ in range(common):
            ba = fa.read(frame_bytes)
            bb = fb.read(frame_bytes)
            if len(ba) != frame_bytes or len(bb) != frame_bytes:
                raise SystemExit("FAIL short read while comparing")
            for channel in range(4):
                va = ba[channel::4]
                vb = bb[channel::4]
                for x, y in zip(va, vb):
                    d = x - y
                    if d:
                        sse[channel] += d * d
                        ad = -d if d < 0 else d
                        if ad > peak[channel]:
                            peak[channel] = ad
    samples = common * width * height
    psnr = []
    for channel in range(4):
        if sse[channel] == 0:
            psnr.append(math.inf)
        else:
            mse = sse[channel] / samples
            psnr.append(10.0 * math.log10((255.0 * 255.0) / mse))
    return common, peak, psnr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, type=pathlib.Path,
                    help="a video ffmpeg can decode; frames are taken from the start")
    ap.add_argument("--tui", required=True, type=pathlib.Path, help="cineform-tui binary")
    ap.add_argument("--work", required=True, type=pathlib.Path, help="scratch directory")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--quality", type=int, default=2)
    ap.add_argument("--alpha", action="store_true")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            # An unmet precondition is a FAIL, never a skip. A skipped check reads
            # exactly like a passing one in a log.
            sys.stderr.write(f"FAIL {tool} is not on PATH; this gate cannot run\n")
            return 1

    width, height = (int(v) for v in args.size.split("x"))
    args.work.mkdir(parents=True, exist_ok=True)
    raw_in = args.work / "source.rgba"
    intermediate = args.work / "intermediate.mkv"
    raw_out = args.work / "decoded.rgba"
    corrupted = args.work / "corrupted.mkv"
    raw_corrupt = args.work / "corrupted.rgba"

    print(f"source      {args.source}")
    print(f"            {probe(args.source, 'stream=codec_name,width,height,pix_fmt')}"
          .replace("\n", " "))

    print("\n1. uncompressed in")
    extract_uncompressed(args.source, raw_in, args.frames, width, height)
    frame_bytes = width * height * 4
    got_frames = raw_in.stat().st_size // frame_bytes
    print(f"   {raw_in.name}: {raw_in.stat().st_size} bytes, {got_frames} frames "
          f"of {width}x{height} RGBA")
    if got_frames == 0:
        sys.stderr.write("FAIL the source yielded no whole frames\n")
        return 1

    print("\n2. encode to the intermediate, over the bus")
    cmd = [str(args.tui), "-i", str(raw_in), "-s", args.size, "-r", "60",
           "-q", str(args.quality), "-frames", str(got_frames), "-nostats"]
    if args.alpha:
        cmd.append("-alpha")
    cmd.append(str(intermediate))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write("   " + proc.stdout.strip().replace("\n", "\n   ") + "\n")
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        sys.stderr.write("FAIL the encode did not succeed\n")
        return 1

    codec = probe(intermediate, "stream=codec_name,codec_tag_string,pix_fmt")
    print(f"   {intermediate.name}: {intermediate.stat().st_size} bytes, {codec}"
          .replace("\n", " "))
    ratio = raw_in.stat().st_size / max(intermediate.stat().st_size, 1)
    print(f"   compression {ratio:.2f}:1")

    print("\n3. decode back to uncompressed")
    decode_to_uncompressed(intermediate, raw_out)
    print(f"   {raw_out.name}: {raw_out.stat().st_size} bytes, "
          f"{raw_out.stat().st_size // frame_bytes} frames")

    print("\n4. compare")
    common, peak, psnr = compare(raw_in, raw_out, width, height, got_frames)
    names = ("R", "G", "B", "A")

    # WHICH CHANNELS ARE UNDER TEST, and this is the whole correctness of the comparison.
    # Without --alpha the encoder is told CFHD_ENCODED_FORMAT_RGB_444, which does not carry
    # alpha at all. The decoded alpha is then whatever the decoder fills in, and comparing
    # it against a source that HAS alpha reports a max error of 255 -- a real difference,
    # and not a codec defect. Measured on a gbrap12le source: R/G/B came back at
    # 53.8/57.4/53.9 dB while A read 3.26 dB, purely because A was never encoded.
    tested = (0, 1, 2, 3) if args.alpha else (0, 1, 2)

    print(f"   {common} frames compared")
    print("   channel  max error  PSNR")
    for i, name in enumerate(names):
        shown = "inf" if psnr[i] == math.inf else f"{psnr[i]:.2f} dB"
        note = "" if i in tested else "   (not encoded: RGB_444 carries no alpha)"
        print(f"   {name}        {peak[i]:>9}  {shown}{note}")

    # The figure the negative control is measured against comes from the tested channels
    # only. Taking max() across all four was the first version of this, and it made the
    # control impossible to pass: alpha's 255 is already the largest a byte can differ by,
    # so no corruption could exceed it and the gate reported that it proved nothing. It
    # was right to.
    clean_worst = max(peak[i] for i in tested)

    failures = 0
    if common != got_frames:
        print(f"   FAIL decoded {common} frames, encoded {got_frames}")
        failures += 1
    else:
        print(f"   ok   frame count survives the round trip ({common})")

    print("\n5. negative control: the same comparison on a corrupted intermediate")
    data = bytearray(intermediate.read_bytes())
    # Corrupt deep inside the payload rather than the header. A broken header makes
    # ffmpeg refuse the file, which would test the demuxer; the claim under test is that
    # the PIXEL comparison notices damage, so the file has to stay decodable.
    start = len(data) // 3
    for i in range(start, min(start + 65536, len(data))):
        data[i] ^= 0xFF
    corrupted.write_bytes(bytes(data))
    decoded_ok = True
    try:
        decode_to_uncompressed(corrupted, raw_corrupt)
    except SystemExit:
        decoded_ok = False

    if not decoded_ok or raw_corrupt.stat().st_size < frame_bytes:
        # Refusing to decode is also a detection, and it is reported as one rather than
        # quietly counted as a pass on a different basis.
        print("   ok   the corrupted file failed to decode at all, which is a detection")
    else:
        c_common, c_peak, c_psnr = compare(raw_in, raw_corrupt, width, height, got_frames)
        worst = max(c_peak[i] for i in tested)
        print(f"   corrupted: {c_common} frames, max error {worst} over tested channels")
        if worst > clean_worst:
            print(f"   ok   corruption is visible: {worst} against {clean_worst} clean")
        else:
            print(f"   FAIL corruption produced no larger error than the clean encode "
                  f"({worst} against {clean_worst}); this comparison proves nothing")
            failures += 1

    print(f"\n{'PASS' if failures == 0 else 'FAIL'}: {failures} failure(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
