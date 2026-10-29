import argparse
import asyncio

from meta_neural_py import Band, Gesture, Streams


async def main(device):
    async with Band(device) as band:
        async with band.subscribe(Streams()) as events:
            async for event in events:
                if isinstance(event, Gesture):
                    print(event.sequence, event.finger, event.action, event.derived_action)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("device")
    asyncio.run(main(parser.parse_args().device))
