"""ONNX Runtime provider selection and fallback tests."""

from types import SimpleNamespace

import depth_ai


class FakeSession:
    def __init__(self, providers):
        self.providers = providers

    def get_providers(self):
        return self.providers


class FakeRuntime:
    class SessionOptions:
        pass

    GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")

    def __init__(self, available, failures=()):
        self.available = available
        self.failures = set(failures)
        self.attempts = []

    def get_available_providers(self):
        return self.available

    def InferenceSession(self, path, *, sess_options, providers, provider_options=None):
        self.attempts.append((providers, provider_options, sess_options))
        if providers[0] in self.failures:
            raise RuntimeError(f"{providers[0]} runtime unavailable")
        return FakeSession(providers)


def test_prefers_cuda_and_sets_gpu_memory_ceiling(monkeypatch):
    runtime = FakeRuntime([
        "CPUExecutionProvider",
        "VulkanExecutionProvider",
        "CUDAExecutionProvider",
    ])
    monkeypatch.setattr(depth_ai, "ort", runtime)

    session = depth_ai.build_optimized_session("depth.onnx")

    providers, provider_options, options = runtime.attempts[0]
    assert providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert provider_options[0]["gpu_mem_limit"] == 2 * 1024 * 1024 * 1024
    assert options.graph_optimization_level == "all"
    assert session.get_providers()[0] == "CUDAExecutionProvider"


def test_falls_through_failed_gpu_provider_to_next_available(monkeypatch):
    runtime = FakeRuntime(
        ["CPUExecutionProvider", "CUDAExecutionProvider", "VulkanExecutionProvider"],
        failures=["CUDAExecutionProvider"],
    )
    monkeypatch.setattr(depth_ai, "ort", runtime)

    session = depth_ai.build_optimized_session("depth.onnx")

    assert [attempt[0][0] for attempt in runtime.attempts] == [
        "CUDAExecutionProvider",
        "VulkanExecutionProvider",
    ]
    assert session.get_providers()[0] == "VulkanExecutionProvider"


def test_falls_back_to_cpu_when_all_accelerators_fail(monkeypatch):
    runtime = FakeRuntime(
        ["CPUExecutionProvider", "CUDAExecutionProvider", "VulkanExecutionProvider"],
        failures=["CUDAExecutionProvider", "VulkanExecutionProvider"],
    )
    monkeypatch.setattr(depth_ai, "ort", runtime)

    session = depth_ai.build_optimized_session("depth.onnx")

    assert [attempt[0] for attempt in runtime.attempts] == [
        ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ["VulkanExecutionProvider", "CPUExecutionProvider"],
        ["CPUExecutionProvider"],
    ]
    assert session.get_providers() == ["CPUExecutionProvider"]


def test_uses_cpu_when_no_accelerator_is_available(monkeypatch):
    runtime = FakeRuntime(["CPUExecutionProvider"])
    monkeypatch.setattr(depth_ai, "ort", runtime)

    session = depth_ai.build_optimized_session("depth.onnx")

    assert len(runtime.attempts) == 1
    assert runtime.attempts[0][0] == ["CPUExecutionProvider"]
    assert session.get_providers() == ["CPUExecutionProvider"]
