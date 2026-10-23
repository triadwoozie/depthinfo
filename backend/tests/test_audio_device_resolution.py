"""Stable PortAudio name matching and compatible full-duplex pairing."""

from types import SimpleNamespace

import pytest

import devices
from config import HardwareConfig


class FakeSoundDevice:
    def __init__(self, entries, default=(0, 1)):
        self.entries = entries
        self.default = SimpleNamespace(device=default)

    def query_devices(self):
        return self.entries


def device(name, hostapi, inputs=0, outputs=0):
    return {
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
    }


def test_resolves_saved_names_after_portaudio_indexes_change(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("HDMI", 0, outputs=2),
            device("USB Audio Mic", 1, inputs=2),
            device("USB Audio Speaker", 1, outputs=2),
        ], default=(1, 2)),
    )
    hardware = HardwareConfig(
        input_audio_device_id=7,
        output_audio_device_id=8,
        input_audio_device_name="USB Audio Mic",
        output_audio_device_name="USB Audio Speaker",
    )

    assert devices.resolve_audio_device_pair(hardware) == (1, 2)


def test_stale_ids_fall_back_to_system_defaults(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("System Mic", 0, inputs=2),
            device("System Speaker", 0, outputs=2),
        ], default=(0, 1)),
    )
    hardware = HardwareConfig(input_audio_device_id=15, output_audio_device_id=16)

    assert devices.resolve_audio_device_pair(hardware) == (0, 1)


def test_system_defaults_take_priority_over_legacy_numeric_ids(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("Default Mic", 0, inputs=2),
            device("Default Speaker", 0, outputs=2),
            device("Unrelated Input", 0, inputs=2),
            device("Unrelated Output", 0, outputs=2),
        ], default=(0, 1)),
    )
    hardware = HardwareConfig(input_audio_device_id=2, output_audio_device_id=3)

    assert devices.resolve_audio_device_pair(hardware) == (0, 1)


def test_incompatible_explicit_devices_use_compatible_system_default_pair(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("Default Mic", 0, inputs=2),
            device("Default Speaker", 0, outputs=2),
            device("USB Mic", 1, inputs=2),
            device("USB Speaker", 2, outputs=2),
        ], default=(0, 1)),
    )
    hardware = HardwareConfig(
        input_audio_device_name="USB Mic",
        output_audio_device_name="USB Speaker",
    )

    assert devices.resolve_audio_device_pair(hardware) == (0, 1)


def test_name_hint_does_not_fall_back_to_a_stale_id(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("Current Mic", 0, inputs=2),
            device("Current Speaker", 0, outputs=2),
        ], default=(0, 1)),
    )
    hardware = HardwareConfig(input_audio_device_id=1, input_audio_device_name="Missing Mic")

    assert devices.resolve_audio_device_id(hardware, "input") == 0


def test_fails_clearly_when_no_compatible_duplex_pair_exists(monkeypatch):
    monkeypatch.setattr(
        devices,
        "sd",
        FakeSoundDevice([
            device("Input", 0, inputs=2),
            device("Output", 1, outputs=2),
        ], default=(0, 1)),
    )
    hardware = HardwareConfig(input_audio_device_id=0, output_audio_device_id=1)

    with pytest.raises(RuntimeError, match="different PortAudio host APIs"):
        devices.resolve_audio_device_pair(hardware)
