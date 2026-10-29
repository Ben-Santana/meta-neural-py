import argparse
import asyncio

from meta_neural_py import Band, Gyroscope, Orientation, Streams


async def main(device):
    async with Band(device) as band:
        async with band.subscribe(
            Streams(gestures=False, gyro=True, orientation=True), overflow="drop_oldest"
        ) as events:
            async for event in events:
                if isinstance(event, (Gyroscope, Orientation)):
                    print(event.timestamp, event.source_clock.value, event.values)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("device")
    asyncio.run(main(parser.parse_args().device))
