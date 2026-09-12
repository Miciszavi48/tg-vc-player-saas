#!/usr/bin/env python3
"""FFmpeg concurrent stream benchmark.

Spawns N concurrent FFmpeg processes using the same optimized parameters
as the music bot audio pipeline, measures CPU and RAM usage via psutil.

Usage:
    python tools/benchmark_stream.py --streams 10
    python tools/benchmark_stream.py --streams 30
    python tools/benchmark_stream.py --streams 50
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time

import psutil

FFMPEG_CMD = [
    "ffmpeg", "-re",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
    "-vn", "-c:a", "libopus", "-b:a", "48k",
    "-threads", "1",
    "-f", "null", "-",
]

SAMPLE_INTERVAL = 1.0
WARMUP_SECONDS = 3
MEASURE_SECONDS = 15


def spawn_streams(n: int) -> list[subprocess.Popen]:
    procs = []
    for _ in range(n):
        p = subprocess.Popen(
            FFMPEG_CMD,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)
    return procs


def measure(procs: list[subprocess.Popen], duration: int) -> dict:
    cpu_samples = []
    rss_samples = []

    for _ in range(duration):
        cpu_pct = psutil.cpu_percent(interval=SAMPLE_INTERVAL)
        cpu_samples.append(cpu_pct)
        total_rss = 0
        alive = 0
        for p in procs:
            try:
                pi = psutil.Process(p.pid)
                total_rss += pi.memory_info().rss
                alive += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        rss_samples.append(total_rss)

    avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0
    peak_cpu = max(cpu_samples) if cpu_samples else 0
    avg_rss = sum(rss_samples) / len(rss_samples) if rss_samples else 0
    peak_rss = max(rss_samples) if rss_samples else 0

    return {
        "avg_cpu_pct": round(avg_cpu, 1),
        "peak_cpu_pct": round(peak_cpu, 1),
        "avg_rss_mb": round(avg_rss / 1024 / 1024, 1),
        "peak_rss_mb": round(peak_rss / 1024 / 1024, 1),
        "alive_count": alive,
        "samples": len(cpu_samples),
    }


def kill_all(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        try:
            p.send_signal(signal.SIGTERM)
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


def run_test(n: int) -> dict:
    print(f"\n{'='*60}")
    print(f"  Spawning {n} concurrent FFmpeg streams ...")
    print(f"{'='*60}")

    procs = spawn_streams(n)
    print(f"  {len(procs)} processes spawned. Warming up {WARMUP_SECONDS}s ...")
    time.sleep(WARMUP_SECONDS)

    alive = sum(1 for p in procs if p.poll() is None)
    print(f"  {alive}/{n} alive after warmup. Measuring for {MEASURE_SECONDS}s ...")

    result = measure(procs, MEASURE_SECONDS)
    result["requested"] = n
    result["per_stream_rss_mb"] = round(result["avg_rss_mb"] / max(alive, 1), 2)

    print(f"\n  Results for {n} streams:")
    print(f"    Alive:       {result['alive_count']}/{n}")
    print(f"    Avg CPU:     {result['avg_cpu_pct']}%")
    print(f"    Peak CPU:    {result['peak_cpu_pct']}%")
    print(f"    Avg RSS:     {result['avg_rss_mb']} MB")
    print(f"    Peak RSS:    {result['peak_rss_mb']} MB")
    print(f"    Per-stream:  {result['per_stream_rss_mb']} MB/stream")

    kill_all(procs)
    time.sleep(1)
    return result


def main():
    parser = argparse.ArgumentParser(description="FFmpeg stream benchmark")
    parser.add_argument("--streams", type=int, nargs="+", default=[10, 30, 50],
                        help="Number of concurrent streams to test")
    args = parser.parse_args()

    print(f"System: {psutil.cpu_count()} CPUs, "
          f"{round(psutil.virtual_memory().total / 1024 / 1024)} MB RAM")

    results = []
    for n in args.streams:
        r = run_test(n)
        results.append(r)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    print(f"  {'Streams':>8} | {'Avg CPU':>8} | {'Peak CPU':>9} | {'Avg RSS':>8} | {'Per-stream':>11}")
    print(f"  {'-'*8} | {'-'*8} | {'-'*9} | {'-'*8} | {'-'*11}")
    for r in results:
        print(f"  {r['requested']:>8} | {r['avg_cpu_pct']:>7}% | {r['peak_cpu_pct']:>8}% | "
              f"{r['avg_rss_mb']:>7}MB | {r['per_stream_rss_mb']:>9}MB")

    breaking_point = None
    for r in results:
        if r["peak_cpu_pct"] > 90:
            breaking_point = r["requested"]
            break

    if breaking_point:
        print(f"\n  BREAKING POINT: {breaking_point} streams (peak CPU > 90%)")
    else:
        print(f"\n  No breaking point found — container handles {results[-1]['requested']} streams fine.")

    return results


if __name__ == "__main__":
    main()
