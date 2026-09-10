import threading
import unittest
from unittest.mock import Mock
import numpy as np
from vision_runtime import VerifiedSession


class VisionRuntimeTests(unittest.TestCase):
    def session(self):
        session = object.__new__(VerifiedSession)
        session.lock = threading.Lock()
        session.cpu, session.gpu = Mock(), Mock()
        session.checked = False
        session.cpu.run.return_value = [np.array([[.1,.9]],dtype=np.float32)]
        session.gpu.run.return_value = [np.array([[.1,.9]],dtype=np.float32)]
        return session

    def test_matching_gpu_validates_once(self):
        session = self.session()
        session.run(None,{})
        session.run(None,{})
        self.assertTrue(session.checked)
        self.assertEqual(session.cpu.run.call_count,1)
        self.assertEqual(session.gpu.run.call_count,2)

    def test_bad_output_disables_gpu(self):
        for output in (np.array([[.9,.1]]),np.array([[float('nan'),.1]])):
            session = self.session()
            session.gpu.run.return_value = [output]
            with self.assertLogs('xiangqi_ai.vision',level='WARNING'):
                result = session.run(None,{})
            self.assertIsNone(session.gpu)
            np.testing.assert_allclose(result[0],[[.1,.9]])

    def test_runtime_failure_falls_back(self):
        session = self.session()
        session.checked = True
        session.gpu.run.side_effect = RuntimeError('device removed')
        with self.assertLogs('xiangqi_ai.vision',level='WARNING'):
            session.run(None,{})
        self.assertIsNone(session.gpu)
        session.cpu.run.assert_called_once()


if __name__ == '__main__': unittest.main()
