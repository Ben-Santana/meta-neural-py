import argparse
import asyncio
import json
import math
import platform
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from . import Band, Gesture, Streams
from .errors import BandError
from .identity.keychain import KeychainIdentityStore
from .identity.store import import_kinesis_record
from .identity.trust import load_point


async def _test_band(device: str, *, duration: float, profile: str) -> None:
    """Timed, sensor-only smoke test. No enrollment or settings writes."""
    profiles = {
        "gestures": Streams(),
        "motion": Streams(gestures=True, gyro=True, orientation=True),
        "emg": Streams(gestures=False, emg=True),
    }
    counts: Counter[str] = Counter()
    async with Band(device) as band:
        print("Host identity accepted and device info confirmed.", flush=True)
        print(f"Band proof verified: {band.band_verified}", flush=True)
        hand = await band.read_hand()
        battery = await band.read_battery()
        print(f"Hand: {hand}; battery: {battery.level if battery else 'unavailable'}", flush=True)
        async with band.subscribe(profiles[profile]) as events:
            print(f"Streams acknowledged. Testing {profile} for {duration:g}s.", flush=True)
            if profile == "gestures":
                print("Make several index and middle finger gestures now.", flush=True)
            deadline = time.monotonic() + duration
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    event = await asyncio.wait_for(anext(events), remaining)
                except TimeoutError:
                    break
                counts[type(event).__name__] += 1
                if isinstance(event, Gesture):
                    print(
                        f"Gesture: {event.finger} {event.action} {event.derived_action}", flush=True
                    )
        print("Stream shutdown acknowledged.", flush=True)
        stats = asdict(band.stats)
    print("Transport closed and device lock released.", flush=True)
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "macos": platform.mac_ver()[0],
                "profile": profile,
                "duration_seconds": duration,
                "events": dict(counts),
                "stats": stats,
            },
            indent=2,
        )
    )
    if not any(counts.get(cls, 0) for cls in ("Gesture", "Gyroscope", "Orientation", "EMGBatch")):
        raise BandError("No sensor events received; this hardware test did not pass")


def _positive_duration(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Duration must be finite and positive")
    return seconds


async def _run(args):
    if args.command == "discover":
        for device in await Band.discover(args.timeout):
            print(json.dumps(asdict(device)))
    elif args.command == "enroll":
        point = None
        if args.band_public_key:
            point = Path(args.band_public_key).read_bytes()
            if len(point) == 65 and point[0] == 4:
                point = point[1:]
            load_point(point)
        if args.allow_missing_band_key:
            print(
                "Compatibility mode: if Meta omits the band key, its proof will remain unverified.",
                flush=True,
            )
        identity = await Band.enroll(
            args.device, band_public_key=point, allow_missing_band_key=args.allow_missing_band_key
        )
        print("Enrollment complete; new host identity confirmed and saved in Keychain.")
        print(
            f"Band public key available for proof verification: {identity.band_public_key is not None}"
        )
    elif args.command == "import-identity":
        identity = import_kinesis_record(Path(args.file).read_bytes())
        band = Band(args.device)
        await asyncio.to_thread(KeychainIdentityStore().save, band.id, identity)
        print("Identity imported. Connect to verify it with the band.")
    elif args.command == "status":
        band = Band(args.device)
        pending = await asyncio.to_thread(band.identity_store.load_pending, band.id)
        saved = await asyncio.to_thread(band.identity_store.load, band.id)
        print(
            json.dumps(
                {
                    "identity_saved": saved is not None,
                    "pending_enrollment_stage": pending.stage if pending else None,
                    "band_public_key_available": saved.band_public_key is not None
                    if saved
                    else False,
                    "allow_missing_band_key": saved.allow_missing_band_key if saved else False,
                }
            )
        )
    elif args.command == "gestures":
        async with Band(args.device) as band:
            async with band.subscribe(Streams()) as events:
                async for event in events:
                    if isinstance(event, Gesture):
                        print(
                            f"{event.sequence} {event.finger} {event.action} {event.derived_action}"
                        )
    elif args.command == "test":
        await _test_band(args.device, duration=args.duration, profile=args.profile)


def main():
    parser = argparse.ArgumentParser(description="Experimental direct Meta Neural Band access")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("discover", help="Read-only Bluetooth discovery")
    scan.add_argument("--timeout", type=float, default=5)
    for name in ("enroll", "gestures", "status", "import-identity", "test"):
        command = commands.add_parser(
            name,
            help={
                "enroll": "Explicitly change ownership using Meta sign-in",
                "gestures": "Print gesture events from the enrolled session",
                "status": "Inspect saved setup state without connecting",
                "import-identity": "Explicitly import a validated identity file",
                "test": "Timed hardware test with acknowledged stream shutdown",
            }[name],
        )
        command.add_argument("device", help="An explicitly selected device UUID from discover")
        if name == "import-identity":
            command.add_argument("file")
        if name == "enroll":
            command.add_argument(
                "--allow-missing-band-key",
                action="store_true",
                help="Explicit Kinesis-compatible mode: allow unverified band proof if Meta omits its key",
            )
            command.add_argument(
                "--band-public-key",
                metavar="FILE",
                help="Explicitly pin a known band P-256 public key (64 raw or 65 SEC1 bytes)",
            )
        if name == "test":
            command.add_argument("--duration", type=_positive_duration, default=60)
            command.add_argument(
                "--profile", choices=("gestures", "motion", "emg"), default="gestures"
            )
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except (BandError, ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
