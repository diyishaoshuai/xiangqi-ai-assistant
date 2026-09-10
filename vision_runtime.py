"""Optional DirectML inference with CPU output validation and runtime fallback."""
import logging
import threading
import numpy as np
import onnxruntime as ort
from performance import resource_settings

LOGGER = logging.getLogger('xiangqi_ai.vision')
ort.disable_telemetry_events()


class VerifiedSession:
    def __init__(self, path, options):
        self.cpu = ort.InferenceSession(path, sess_options=options, providers=['CPUExecutionProvider'])
        self.gpu = None
        self.checked = False
        self.lock = threading.Lock()
        device = resource_settings()['vision_device']
        if device >= 0 and 'DmlExecutionProvider' in ort.get_available_providers():
            try:
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                self.gpu = ort.InferenceSession(path, sess_options=options,
                    providers=[('DmlExecutionProvider', {'device_id': str(device)}), 'CPUExecutionProvider'])
                if 'DmlExecutionProvider' not in self.gpu.get_providers():
                    self.gpu = None
            except Exception:
                LOGGER.warning('GPU init failed; using CPU model=%s', path, exc_info=True)
        LOGGER.info('vision backend model=%s gpu=%s device=%s', path, self.gpu is not None, device)

    def __getattr__(self, name):
        return getattr(self.cpu, name)

    def get_providers(self):
        return (self.gpu or self.cpu).get_providers()

    def run(self, outputs, feed):
        # DirectML sessions do not support concurrent Run calls.
        with self.lock:
            if self.gpu is None:
                return self.cpu.run(outputs, feed)
            try:
                result = self.gpu.run(outputs, feed)
                if not all(np.isfinite(value).all() for value in result):
                    raise ValueError('Non-finite GPU output')
                if not self.checked:
                    reference = self.cpu.run(outputs, feed)
                    if len(reference) != len(result) or not all(
                        a.shape == b.shape and np.allclose(a, b, rtol=1e-4, atol=1e-5)
                        and np.array_equal(a.argmax(-1), b.argmax(-1))
                        for a, b in zip(reference, result)
                    ):
                        raise ValueError('GPU/CPU output validation failed')
                    self.checked = True
                return result
            except Exception:
                LOGGER.warning('GPU inference disabled for this session; using CPU', exc_info=True)
                self.gpu = None
                return self.cpu.run(outputs, feed)
