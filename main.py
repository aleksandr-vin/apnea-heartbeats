import asyncio

# import sounddevice as sd
import numpy as np
import queue
from bleak import BleakClient, BleakScanner
from datetime import datetime, UTC
from typing import Literal
import pandas as pd
import json

# from vosk import Model, KaldiRecognizer
import typer
from rich import print


HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
SPO2_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
PULSE_RATE_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

last_heartbeat_time = None
q = queue.Queue()
data_log = []

# model = Model(lang="en-us")  # Download from https://alphacephei.com/vosk/models
# recognizer = KaldiRecognizer(model, 16000)


class AppRuntimeError(RuntimeError):
    """Common app runtime error."""


class DeviceNotFoundError(AppRuntimeError):
    """Device not found."""


class FailedToConnectError(AppRuntimeError):
    """Failed to connect."""


def audio_callback(indata, frames, time, status):
    if status:
        print(status)
    q.put(bytes(indata))


def parse_heart_rate_and_rr(data):
    offset = 1
    hr_value = data[offset]
    offset += 1
    if data[0] & 0x01:
        hr_value = int.from_bytes(data[offset : offset + 1], byteorder="little")
        offset += 1

    rr_intervals = []
    if data[0] & 0x10:
        while offset < len(data):
            rr = int.from_bytes(data[offset : offset + 2], byteorder="little") / 1024.0
            rr_intervals.append(rr)
            offset += 2

    return hr_value, rr_intervals


async def heart_rate_handler(sender, data):
    hr, rr_intervals = parse_heart_rate_and_rr(data)
    current_time = datetime.now()

    for rr in rr_intervals:
        entry = {
            "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "type": "heartbeat",
            "value": hr,
            "rr_interval_sec": rr,
        }
        data_log.append(entry)
        print(entry)


def parse_spo2(data):
    # Implement parsing logic based on the device's data format
    spo2_value = int.from_bytes(data, byteorder="little")
    return spo2_value


def parse_pulse_rate(data):
    # Implement parsing logic based on the device's data format
    pulse_rate = int.from_bytes(data, byteorder="little")
    return pulse_rate


async def spo2_handler(sender, data):
    spo2 = parse_spo2(data)
    current_time = datetime.now()
    entry = {
        "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "type": "SpO2",
        "value": spo2,
    }
    data_log.append(entry)
    print(entry)


async def pulse_rate_handler(sender, data):
    pulse_rate = parse_pulse_rate(data)
    current_time = datetime.now()
    entry = {
        "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "type": "Pulse Rate",
        "value": pulse_rate,
    }
    data_log.append(entry)
    print(entry)


async def record_audio():
    with sd.RawInputStream(
        samplerate=16000,
        blocksize=8000,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        while True:
            data = q.get()
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text in ["breathe", "hold"]:
                    current_time = datetime.now()
                    entry = {
                        "timestamp": current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        "type": "command",
                        "value": text,
                        "rr_interval_sec": None,
                    }
                    data_log.append(entry)
                    print(entry)


async def measurement_loop(keyname: str, sensor_types: list[Literal["hr", "oxymeter"]]):
    print("[yellow]Scanning for sensors...[/]")
    devices = await BleakScanner.discover()
    print(devices)

    device = next(
        (
            d
            for d in devices
            if d.name is not None and keyname.lower() in d.name.lower()
        ),
        None,
    )

    if not device:
        print(f"[red]Device with [yellow]{keyname}[/yellow] [red]not found.[/]")
        raise DeviceNotFoundError(f"Device with {keyname} not found")

    print(f"[yellow]Connecting to [blue]{device.name}[/blue]...[/]")
    async with BleakClient(device) as client:
        if not client.is_connected:
            print("[red]Failed to connect.[/]")
            raise FailedToConnectError("Failed to connect")

        if "hr" in sensor_types:
            print("[yellow]Subscribing to heart rate measurements...[/]")
            await client.start_notify(HR_MEASUREMENT_UUID, heart_rate_handler)
        if "oxymeter" in sensor_types:
            print("[yellow]Subscribing to SpO2 and Pulse Rate measurements...[/]")
            await client.start_notify(SPO2_CHARACTERISTIC_UUID, spo2_handler)
            # await client.start_notify(PULSE_RATE_CHARACTERISTIC_UUID, pulse_rate_handler)

        # audio_task = asyncio.create_task(record_audio())

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            # audio_task.cancel()
            if "hr" in sensor_types:
                print("[yellow]Stopping heart rate monitoring...[/]")
                await client.stop_notify(HR_MEASUREMENT_UUID)
            if "oxymeter" in sensor_types:
                print("[yellow]Stopping oximeter monitoring...[/]")
                await client.stop_notify(SPO2_CHARACTERISTIC_UUID)
                # await client.stop_notify(PULSE_RATE_CHARACTERISTIC_UUID)

        finally:
            df = pd.DataFrame(data_log)
            filename = f"measurements-{datetime.now(tz=UTC)}.csv"
            df.to_csv(filename, index=False)
            print(f"[yellow]Data saved to {filename}.[/]")


def main():
    async def loop():
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(
                    measurement_loop(keyname="OxySmart", sensor_types={"oxymeter"})
                )
                tg.create_task(
                    measurement_loop(keyname="Polar H10", sensor_types={"hr"})
                )
        except ExceptionGroup as err:
            print(err.exceptions)

    asyncio.run(loop())


if __name__ == "__main__":
    typer.run(main)
