import time
import uuid
from collections import defaultdict

class BenchmarkCollector:

    def __init__(self):
        self.active = False
        self.benchmark_id = None
        self.start_time = None
        self.end_time = None
        self.requests = []
        self.config = {}

    def start(self, config):
        self.active = True
        self.benchmark_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.config = config
        self.requests = []

    def stop(self):
        self.end_time = time.time()
        self.active = False

    def record(self, record):
        if not self.active:
            return
        self.requests.append(record)

    def summary_by_node(self):
        nodes = defaultdict(list)

        for r in self.requests:
            nodes[r["node"]].append(r)

        out = {}

        for node, reqs in nodes.items():
            total_tokens = sum(r["completion_tokens"] for r in reqs)
            total_gen = sum(r["generation_time"] for r in reqs)

            out[node] = {
                "requests": len(reqs),
                "completion_tokens": total_tokens,
                "tokens_per_sec": total_tokens / total_gen if total_gen else 0,
                "avg_latency": sum(r["latency"] for r in reqs) / len(reqs),
                "avg_ttft": sum(r["ttft"] for r in reqs) / len(reqs),
            }

        return out


BENCHMARK = BenchmarkCollector()