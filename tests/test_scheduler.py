"""Priority and single-worker isolation regression tests."""
from threading import Event, Lock
import unittest
from app.scheduler import PriorityExecutor


class SchedulerTests(unittest.TestCase):
    def test_priority_and_serial_inference(self):
        worker = PriorityExecutor()
        started, release = Event(), Event()
        events, mutex = [], Lock()
        active = [0]
        peak = [0]

        def task(label, block=False):
            with mutex:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            if block:
                started.set()
                if not release.wait(3):
                    raise TimeoutError("test could not release worker")
            events.append(label)
            with mutex:
                active[0] -= 1
            return label

        try:
            first = worker.submit(task, "first", True, priority=20)
            self.assertTrue(started.wait(2))
            batch = worker.submit(task, "batch", priority=20)
            upload = worker.submit(task, "upload", priority=10)
            live = worker.submit(task, "live", priority=0)
            release.set()
            for f in (first, batch, upload, live):
                f.result(timeout=4)
            self.assertEqual(events, ["first", "live", "upload", "batch"])
            self.assertEqual(peak[0], 1)
        finally:
            release.set()
            worker.shutdown()


if __name__ == "__main__":
    unittest.main()
