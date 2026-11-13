"""Give every onnxruntime session an explicit thread count.

Left to itself onnxruntime sizes its pool from the host's CPU count and pins each thread
to a core. Inside LXC 209 the cpuset is not 0..N-1, so the pinning targets CPUs the
container does not own (`pthread_setaffinity_np failed`, OPS-2026-11-13-13) and the pool
is sized for the host, not for the ingest service's `cpus: 2`. With an explicit count
onnxruntime does not set affinity at all.

insightface builds its sessions internally and passes no SessionOptions through, so the
default is installed on the class. Call `pin()` before loading any model.
"""
import os

THREADS = int(os.environ.get("ORT_THREADS", "2"))   # matches compose `cpus: 2`

_done = False


def pin(threads: int | None = None) -> int:
    global _done
    n = threads or THREADS
    if _done:
        return n
    import onnxruntime as ort

    orig = ort.InferenceSession.__init__

    def init(self, path_or_bytes, sess_options=None, *args, **kwargs):
        if sess_options is None:
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = n
            sess_options.inter_op_num_threads = 1
        orig(self, path_or_bytes, sess_options, *args, **kwargs)

    ort.InferenceSession.__init__ = init
    _done = True
    return n
