import argparse
import asyncio

from meta_neural_py import Band, Streams
from meta_neural_py.recording import record


async def main(device, output):
    async with Band(device) as band:
        # EMG-only is the initial profile; mixed throughput is unvalidated.
        async with band.subscribe(Streams(gestures=False, emg=True)) as events:
            await record(events, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("device")
    parser.add_argument("output")
    args = parser.parse_args()
    asyncio.run(main(args.device, args.output))
