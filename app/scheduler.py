"""One inference lane, with stable priorities: live(0) > upload(10) > batch(20)."""
from concurrent.futures import Future
from itertools import count
from queue import PriorityQueue
from threading import Lock, Thread
from typing import Any, Callable


class PriorityExecutor:
    """Single CPU worker, ordered by priority and FIFO within a priority."""
    def __init__(self) -> None:
        self._queue: PriorityQueue = PriorityQueue()
        self._sequence = count()
        self._lock = Lock()
        self._closed = False
        self._thread = Thread(target=self._work, name="whisper-cpu", daemon=True)
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any, priority: int = 10, **kwargs: Any) -> Future:
        future: Future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("inference executor is shut down")
            self._queue.put((priority, next(self._sequence), future, fn, args, kwargs))
        return future

    def _work(self) -> None:
        while True:
            priority, number, future, fn, args, kwargs = self._queue.get()
            try:
                if fn is None:
                    return
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(fn(*args, **kwargs))
                    except BaseException as exc:
                        future.set_exception(exc)
            finally:
                self._queue.task_done()

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put((float("inf"), next(self._sequence), Future(), None, (), {}))
        if wait:
            self._thread.join()
