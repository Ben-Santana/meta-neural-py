"""Private versioned IPC worker. Cocoa always executes on this process's main thread."""

from __future__ import annotations

import base64
import json
import queue
import sys
import threading
import time
import uuid

from .base import bluetooth_uuid

VERSION = 1
LIMIT = 131072


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("login", "login-smoke"):
        from ..identity._login import main as login_main

        login_main(smoke_test=sys.argv[1] == "login-smoke")
        return
    try:
        import CoreBluetooth as CB
        import Foundation as F
        import objc
    except ImportError:
        print(
            json.dumps(
                {
                    "version": VERSION,
                    "event": "error",
                    "kind": "dependency",
                    "message": "Install macOS PyObjC framework dependencies",
                }
            ),
            flush=True,
        )
        return

    commands: queue.Queue = queue.Queue(maxsize=128)
    output: queue.Queue = queue.Queue(maxsize=128)
    done = threading.Event()
    eof = threading.Event()

    def emit(event, **values):
        try:
            output.put_nowait({"version": VERSION, "event": event, **values})
        except queue.Full:
            while True:
                try:
                    output.get_nowait()
                except queue.Empty:
                    break
            output.put_nowait(
                {
                    "version": VERSION,
                    "event": "error",
                    "kind": "overflow",
                    "message": "Native IPC receive buffer overflow",
                }
            )
            done.set()

    def fail(message, kind="transport"):
        emit("error", kind=kind, message=message)
        done.set()

    def read_commands():
        try:
            while not done.is_set():
                line = sys.stdin.buffer.readline(LIMIT + 1)
                if not line:
                    break
                if len(line) > LIMIT:
                    fail("Native IPC command too large")
                    break
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("version") != VERSION:
                    fail("Native IPC version mismatch")
                    break
                commands.put(message, timeout=1)
        except Exception:
            fail("Native IPC input failed")
        finally:
            eof.set()

    def write_events():
        try:
            while True:
                value = output.get()
                if value is None:
                    break
                sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except (BrokenPipeError, OSError):
            done.set()

    reader = threading.Thread(target=read_commands, daemon=True)
    writer = threading.Thread(target=write_events, daemon=True)
    reader.start()
    writer.start()

    class Delegate(
        F.NSObject,
        protocols=[
            objc.protocolNamed("CBCentralManagerDelegate"),
            objc.protocolNamed("CBPeripheralDelegate"),
            objc.protocolNamed("NSStreamDelegate"),
        ],
    ):
        def init(self):
            self = objc.super(Delegate, self).init()
            if self is None:
                return None
            self.central = None
            self.peripheral = None
            self.channel = None
            self.mode = None
            self.target = None
            self.devices = {}
            self.deadline = float("inf")
            self.outgoing = bytearray()
            self.battery = None
            self.next_battery = 0
            return self

        @objc.python_method
        def command(self, message):
            operation = message.get("op")
            if operation in ("scan", "connect"):
                if self.mode is not None:
                    fail("Native operation already running")
                    return
                self.mode = operation
                self.target = str(uuid.UUID(message["id"])) if operation == "connect" else None
                self.deadline = time.monotonic() + float(message["timeout"])
                self.central = CB.CBCentralManager.alloc().initWithDelegate_queue_options_(
                    self, None, {CB.CBCentralManagerOptionShowPowerAlertKey: False}
                )
            elif operation == "write":
                data = base64.b64decode(message["data"], validate=True)
                if len(self.outgoing) + len(data) > 1_048_576:
                    fail("Native write buffer overflow", "overflow")
                    return
                self.outgoing.extend(data)
                self.flush()
            else:
                fail("Unknown native IPC command")

        @objc.python_method
        def flush(self):
            if self.channel is None:
                return
            stream = self.channel.outputStream()
            while self.outgoing and stream.hasSpaceAvailable():
                chunk = bytes(self.outgoing[:32768])
                count = stream.write_maxLength_(chunk, len(chunk))
                if count < 0:
                    fail("L2CAP write failed")
                    return
                if count == 0:
                    return
                del self.outgoing[:count]

        def centralManagerDidUpdateState_(self, central):
            state = central.state()
            if state == CB.CBManagerStatePoweredOn:
                if self.peripheral is not None:
                    return
                if self.target:
                    known = central.retrievePeripheralsWithIdentifiers_(
                        [F.NSUUID.alloc().initWithUUIDString_(self.target)]
                    )
                    if known:
                        self.connect(known[0])
                        return
                central.scanForPeripheralsWithServices_options_(None, None)
            elif state == CB.CBManagerStateUnauthorized:
                fail("Allow Bluetooth access for the Python host in System Settings", "permission")
            elif state == CB.CBManagerStateUnsupported:
                fail("Bluetooth is unsupported on this Mac", "unsupported")
            elif state == CB.CBManagerStatePoweredOff:
                fail("Turn Bluetooth on")
            elif state == CB.CBManagerStateResetting and self.peripheral is not None:
                fail("Bluetooth is restarting")

        def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(
            self, central, peripheral, advertisement, rssi
        ):
            identifier = str(peripheral.identifier().UUIDString())
            name = str(
                advertisement.get(CB.CBAdvertisementDataLocalNameKey) or peripheral.name() or ""
            )
            if self.mode == "scan" and name.lower().startswith("meta band"):
                self.devices[identifier] = {"id": identifier, "name": name, "rssi": int(rssi)}
            elif (
                self.mode == "connect"
                and identifier.lower() == self.target.lower()
                and self.peripheral is None
            ):
                self.connect(peripheral)

        @objc.python_method
        def connect(self, peripheral):
            self.peripheral = peripheral
            self.central.stopScan()
            peripheral.setDelegate_(self)
            self.central.connectPeripheral_options_(peripheral, None)

        def centralManager_didConnectPeripheral_(self, central, peripheral):
            peripheral.discoverServices_(
                [CB.CBUUID.UUIDWithString_("FEB8"), CB.CBUUID.UUIDWithString_("180F")]
            )

        def centralManager_didFailToConnectPeripheral_error_(self, central, peripheral, error):
            fail("Could not connect to band; close other band applications and try again")

        def centralManager_didDisconnectPeripheral_error_(self, central, peripheral, error):
            emit("disconnected")
            done.set()

        def peripheral_didDiscoverServices_(self, peripheral, error):
            if error is not None:
                fail("Band service discovery failed")
                return
            found = False
            for service in peripheral.services() or []:
                service_id = bluetooth_uuid(str(service.UUID().UUIDString()))
                if service_id == bluetooth_uuid("FEB8"):
                    found = True
                    peripheral.discoverCharacteristics_forService_(
                        [CB.CBUUID.UUIDWithString_("2D41DA7C-82B6-42AA-B34E-E2E01DF8CC1A")], service
                    )
                elif service_id == bluetooth_uuid("180F"):
                    peripheral.discoverCharacteristics_forService_(
                        [CB.CBUUID.UUIDWithString_("2A19")], service
                    )
            if not found:
                identifiers = [str(s.UUID().UUIDString()) for s in peripheral.services() or []]
                fail(
                    "Band does not expose the expected FEB8 service; discovered services: "
                    + ", ".join(identifiers)
                )

        def peripheral_didDiscoverCharacteristicsForService_error_(
            self, peripheral, service, error
        ):
            if bluetooth_uuid(str(service.UUID().UUIDString())) == bluetooth_uuid("180F"):
                if error is None:
                    for characteristic in service.characteristics() or []:
                        if bluetooth_uuid(
                            str(characteristic.UUID().UUIDString())
                        ) == bluetooth_uuid("2A19"):
                            self.battery = characteristic
                return
            if error is not None:
                fail("Band PSM characteristic discovery failed")
                return
            found = False
            for characteristic in service.characteristics() or []:
                if (
                    bluetooth_uuid(str(characteristic.UUID().UUIDString()))
                    == "2D41DA7C-82B6-42AA-B34E-E2E01DF8CC1A"
                ):
                    found = True
                    peripheral.readValueForCharacteristic_(characteristic)
            if not found:
                fail("Band PSM characteristic is missing")

        def peripheral_didUpdateValueForCharacteristic_error_(
            self, peripheral, characteristic, error
        ):
            if bluetooth_uuid(str(characteristic.UUID().UUIDString())) == bluetooth_uuid("2A19"):
                if error is None:
                    data = bytes(characteristic.value() or b"")
                    if len(data) == 1 and data[0] <= 100:
                        emit("battery", level=data[0])
                return
            if error is not None:
                fail(
                    "System pairing failed; accept the Bluetooth prompt. If a reset band has an old Bluetooth entry, forget that entry and pair again"
                )
                return
            data = bytes(characteristic.value() or b"")
            if data != b"\xff\x00":
                fail("Unsupported band L2CAP PSM")
                return
            peripheral.openL2CAPChannel_(255)

        def peripheral_didOpenL2CAPChannel_error_(self, peripheral, channel, error):
            if error is not None or channel is None:
                fail("Could not open band L2CAP channel")
                return
            self.channel = channel
            self.deadline = float("inf")
            for stream in (channel.inputStream(), channel.outputStream()):
                stream.setDelegate_(self)
                stream.scheduleInRunLoop_forMode_(F.NSRunLoop.mainRunLoop(), F.NSRunLoopCommonModes)
                stream.open()
            emit("connected")

        def stream_handleEvent_(self, stream, event):
            if event & (F.NSStreamEventErrorOccurred | F.NSStreamEventEndEncountered):
                fail("Band L2CAP stream closed")
                return
            if event & F.NSStreamEventHasBytesAvailable:
                count, data = stream.read_maxLength_(None, 65536)
                if count < 0:
                    fail("Band L2CAP read failed")
                elif count > 0:
                    emit("data", data=base64.b64encode(bytes(data[:count])).decode("ascii"))
            if event & F.NSStreamEventHasSpaceAvailable:
                self.flush()

        @objc.python_method
        def tick(self):
            now = time.monotonic()
            if self.battery is not None and self.channel is not None and now >= self.next_battery:
                self.next_battery = now + 60
                self.peripheral.readValueForCharacteristic_(self.battery)
            if now >= self.deadline:
                if self.mode == "scan":
                    self.central.stopScan()
                    emit(
                        "devices",
                        devices=sorted(
                            self.devices.values(), key=lambda d: d["rssi"], reverse=True
                        ),
                    )
                    self.deadline = float("inf")
                else:
                    fail("Bluetooth pairing/L2CAP deadline expired")

        @objc.python_method
        def close(self):
            if self.channel is not None:
                for stream in (self.channel.inputStream(), self.channel.outputStream()):
                    stream.close()
                    stream.removeFromRunLoop_forMode_(
                        F.NSRunLoop.mainRunLoop(), F.NSRunLoopCommonModes
                    )
                    stream.setDelegate_(None)
            if self.central is not None:
                self.central.stopScan()
                if self.peripheral is not None:
                    self.peripheral.setDelegate_(None)
                    self.central.cancelPeripheralConnection_(self.peripheral)
                self.central.setDelegate_(None)

    delegate = Delegate.alloc().init()
    emit("hello")
    try:
        while not done.is_set() and not eof.is_set():
            with objc.autorelease_pool():
                for _ in range(32):
                    try:
                        command = commands.get_nowait()
                    except queue.Empty:
                        break
                    delegate.command(command)
                delegate.tick()
                F.NSRunLoop.currentRunLoop().runMode_beforeDate_(
                    F.NSDefaultRunLoopMode, F.NSDate.dateWithTimeIntervalSinceNow_(0.01)
                )
    except Exception:
        fail("Native Bluetooth worker failed")
    finally:
        delegate.close()
        try:
            output.put(None, timeout=0.5)
        except queue.Full:
            pass
        writer.join(timeout=1)


if __name__ == "__main__":
    main()
