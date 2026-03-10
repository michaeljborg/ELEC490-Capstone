import time
import uuid
import math
from collections import defaultdict



def percentile(values, p):
    if not values:
        return 0
    values = sorted(values)
    k = int(len(values) * p)
    if k >= len(values):
        k = len(values) - 1
    return values[k]

class BenchmarkCollector:

    def system_summary(self):

        total_requests = len(self.requests)

        if total_requests == 0:
            return {}

        total_tokens = sum(r["completion_tokens"] for r in self.requests)

        latencies = [r["latency"] for r in self.requests]
        ttfts = [r["ttft"] for r in self.requests]

        wall_time = self.end_time - self.start_time if self.end_time and self.start_time else 0

        avg_latency = sum(latencies) / len(latencies)
        avg_ttft = sum(ttfts) / len(ttfts)

        p95_latency = percentile(latencies, 0.95)
        p95_ttft = percentile(ttfts, 0.95)

        # batching metrics
        batch_size = self.config.get("batch_size", 1)
        nodes = self.config.get("nodes", 1)

        tokens_per_request = total_tokens / total_requests if total_requests else 0


        capacity_per_round = nodes * batch_size

        rounds = math.ceil(total_requests / capacity_per_round) if capacity_per_round else 0

        total_capacity = rounds * capacity_per_round if rounds else 0

        batch_utilization = (
            total_requests / total_capacity
            if total_capacity else 0
        )

        return {
            "benchmark_duration": wall_time,
            "batch_size": batch_size,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "tokens_per_request": tokens_per_request,
            "cluster_tokens_per_sec": total_tokens / wall_time if wall_time else 0,
            "avg_latency": avg_latency,
            "avg_ttft": avg_ttft,
            "batch_utilization": batch_utilization
        }


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