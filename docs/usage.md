# Python usage

Enroll explicitly using [getting started](getting-started.md) first. Use the
UUID you selected with `meta-neural discover` in place of `DEVICE_UUID`.

```python
import asyncio

from meta_neural_py import Band, Gesture, Streams


async def main():
    async with Band("DEVICE_UUID") as band:
        print(await band.read_hand())
        print(await band.read_battery())
        async with band.subscribe(Streams(gestures=True)) as events:
            async for event in events:
                if isinstance(event, Gesture):
                    print(event.finger, event.action)


asyncio.run(main())
```

Run normal scripts with `asyncio.run`; in an existing event loop, await `main()`.
Notebook and GUI host behavior remains unvalidated. Context exit stops streams
and closes the connection, including on cancellation. Only one subscription is
allowed per `Band`.

## Motion and EMG

Use `Streams(gestures=False, gyro=True, orientation=True)` for motion, or
`Streams(gestures=False, emg=True)` for EMG. Mixed EMG/motion throughput remains
unvalidated. Motion retains wire units; orientation is `w,x,y,z` with unvalidated
physical coordinate conventions. EMG batches are unsigned 16 by 8 ADC counts,
not calibrated voltages. Install the `numpy` extra for `EMGBatch.as_numpy()`.

The source checkout includes runnable examples:

```sh
python examples/gestures.py DEVICE_UUID
python examples/motion.py DEVICE_UUID
python examples/record_emg.py DEVICE_UUID recording.jsonl
```

Recordings contain sensor data and may be private. The recorder refuses to
overwrite an existing file. See [data format and replay](data-format.md).

## Backpressure and reconnect

The default subscription fails visibly on queue overflow. For a realtime display,
choose `overflow="drop_oldest"` or `"drop_newest"` explicitly and monitor
`band.stats.queue_drops`. Byte and EMG sample budgets also bound the queue.
Keep CPU-heavy processing off the asyncio event loop.

Reconnect is opt-in: construct
`Band("DEVICE_UUID", reconnect=ReconnectPolicy(attempts=2))` after importing
`ReconnectPolicy` from `meta_neural_py`. A recovered subscription emits a
`Discontinuity`; identity and proof rejection do not trigger enrollment.

See the [API guide](api.md) for configuration and
[troubleshooting](troubleshooting.md) for error handling.
