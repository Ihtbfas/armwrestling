"""Bounded optional JSONL trace. Disk I/O stays off the serial control loop."""
import json
from queue import Empty, Full, Queue
from threading import Event, Thread


class ExperimentLog:
    def __init__(self, path, capacity=4096):
        self.file = open(path, 'w', encoding='utf-8')
        self.pending = Queue(maxsize=capacity)
        self.done = Event()
        self.omitted = self.write_failed = 0
        self.error = None
        self.worker = Thread(target=self._write, daemon=True)
        self.worker.start()

    def record(self, row):
        if self.error is not None:
            self.omitted += 1
            return
        try:
            self.pending.put_nowait(row)
        except Full:
            self.omitted += 1

    @property
    def dropped(self):
        return self.omitted + self.write_failed

    def _write(self):
        current = None
        try:
            with self.file as output:
                while not self.done.is_set() or not self.pending.empty():
                    try:
                        current = self.pending.get(timeout=.05)
                    except Empty:
                        continue
                    output.write(json.dumps(current, ensure_ascii=False, allow_nan=False) + '\n')
                    if self.pending.empty():
                        output.flush()
                    current = None
                output.write(json.dumps(dict(type='trace_summary', dropped=self.dropped)) + '\n')
        except OSError as error:
            self.error = str(error)
            self.write_failed = self.pending.qsize() + int(current is not None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.done.set()
        self.worker.join(timeout=1.)
        if self.worker.is_alive():
            self.error = 'experiment log did not finish flushing'
