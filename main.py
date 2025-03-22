import asyncio
# import sounddevice as sd
import numpy as np
import queue
from bleak import BleakClient, BleakScanner
from datetime import datetime, UTC
import pandas as pd
import json
# from vosk import Model, KaldiRecognizer
import typer
from rich import print


HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

last_heartbeat_time = None
q = queue.Queue()
data_log = []

# model = Model(lang="en-us")  # Download from https://alphacephei.com/vosk/models
# recognizer = KaldiRecognizer(model, 16000)


def audio_callback(indata, frames, time, status):
    if status:
        print(status)
    q.put(bytes(indata))


def parse_heart_rate_and_rr(data):
    offset = 1
    hr_value = data[offset]
    offset += 1
    if data[0] & 0x01:
        hr_value = int.from_bytes(data[offset:offset+1], byteorder="little")
        offset += 1

    rr_intervals = []
    if data[0] & 0x10:
        while offset < len(data):
            rr = int.from_bytes(data[offset:offset+2], byteorder="little") / 1024.0
            rr_intervals.append(rr)
            offset += 2

    return hr_value, rr_intervals


async def heart_rate_handler(sender, data):
    hr, rr_intervals = parse_heart_rate_and_rr(data)
    current_time = datetime.now()

    for rr in rr_intervals:
        entry = {
            'timestamp': current_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
            'type': 'heartbeat',
            'value': hr,
            'rr_interval_sec': rr
        }
        data_log.append(entry)
        print(entry)


async def record_audio():
    with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16', channels=1, callback=audio_callback):
        while True:
            data = q.get()
            if recognizer.AcceptWaveform(data):
                result = json.loads(recognizer.Result())
                text = result.get("text", "")
                if text in ["breathe", "hold"]:
                    current_time = datetime.now()
                    entry = {
                        'timestamp': current_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
                        'type': 'command',
                        'value': text,
                        'rr_interval_sec': None,
                    }
                    data_log.append(entry)
                    print(entry)


async def connect_polar_sensor():
    print("[yellow]Scanning for Polar heart rate sensors...[/]")
    devices = await BleakScanner.discover()

    polar_device = next((d for d in devices if d.name is not None and "Polar" in d.name), None)

    if not polar_device:
        print("[red]Polar device not found.[/]")
        return

    print(f"[yellow]Connecting to [blue]{polar_device.name}[/blue]...[/]")
    async with BleakClient(polar_device) as client:
        if not client.is_connected:
            print("[red]Failed to connect.[/]")
            return

        print("[yellow]Subscribing to heart rate measurements...[/]")
        await client.start_notify(HR_MEASUREMENT_UUID, heart_rate_handler)

        # audio_task = asyncio.create_task(record_audio())

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("[yellow]Stopping heart rate monitoring...[/]")
            await client.stop_notify(HR_MEASUREMENT_UUID)
            # audio_task.cancel()

        finally:
            df = pd.DataFrame(data_log)
            filename = f'measurements-{datetime.now(tz=UTC)}.csv'
            df.to_csv(filename, index=False)
            print(f"[yellow]Data saved to {filename}.[/]")



def main():
    asyncio.run(connect_polar_sensor())

if __name__ == "__main__":
    typer.run(main)
