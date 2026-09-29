#!/usr/bin/env python3
"""Compare consumer build and lookup costs for two rust-i18n checkouts.

Dependencies are warmed before each measured consumer build. Runs are serialized
and guarded against host memory, swap, disk, and time exhaustion.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

from gen_corpus import generate


TIERS = (
    ("small", 8, 1_000, 3),
    ("medium", 16, 1_000, 3),
    ("large", 32, 1_000, 2),
    ("xlarge", 49, 17_630, 1),
)
SOURCE = r'''#[macro_use]
extern crate rust_i18n;

i18n!("locales");

fn main() {
    use std::hint::black_box;
    use std::time::Instant;

    let started = Instant::now();
    let first = t!(__KEY__, locale = "en");
    let first_ns = started.elapsed().as_nanos();
    assert!(first.starts_with("en: "), "unexpected translation: {first}");

    const ITERATIONS: u32 = 1_000_000;
    let started = Instant::now();
    let mut checksum = 0usize;
    for _ in 0..ITERATIONS {
        let key = black_box(__KEY__);
        let locale = black_box("en");
        checksum += black_box(t!(key, locale = locale)).len();
    }
    let lookup_ns = started.elapsed().as_nanos() as f64 / f64::from(ITERATIONS);
    println!("bench_result {{\"first_ns\":{},\"lookup_ns\":{:.2},\"checksum\":{}}}",
             first_ns, lookup_ns, checksum);
}
'''


def sha256_corpus(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.yml")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def git_sha(checkout: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()


def group_rss_kib(pgid: int) -> tuple[int, int]:
    output = subprocess.check_output(["ps", "-A", "-o", "pgid=,rss=,comm="], text=True)
    group = rustc = 0
    for line in output.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or int(fields[0]) != pgid:
            continue
        resident = int(fields[1])
        group += resident
        if "rustc" in fields[2]:
            rustc = max(rustc, resident)
    return group, rustc


def free_swap_mib() -> float | None:
    if sys.platform == "darwin":
        output = subprocess.check_output(["sysctl", "vm.swapusage"], text=True)
        match = re.search(r"free = ([0-9.]+)M", output)
        return float(match.group(1)) if match else None
    if sys.platform == "linux":
        match = re.search(r"^SwapFree:\s+(\d+) kB", Path("/proc/meminfo").read_text(), re.M)
        return int(match.group(1)) / 1024 if match else None
    return None


def free_memory_percent() -> float | None:
    if sys.platform == "darwin":
        output = subprocess.check_output(["memory_pressure", "-Q"], text=True)
        match = re.search(r"System-wide memory free percentage: (\d+)%", output)
        return float(match.group(1)) if match else None
    if sys.platform == "linux":
        data = Path("/proc/meminfo").read_text()
        available = re.search(r"^MemAvailable:\s+(\d+) kB", data, re.M)
        total = re.search(r"^MemTotal:\s+(\d+) kB", data, re.M)
        if available and total:
            return 100 * int(available.group(1)) / int(total.group(1))
    return None


def stop_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    time.sleep(1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_guarded(command: list[str], cwd: Path, env: dict, log: Path, args) -> dict:
    time_cmd = ["/usr/bin/time", "-l"] if sys.platform == "darwin" else ["/usr/bin/time", "-v"]
    started = time.monotonic()
    peak_group = peak_rustc = 0
    reason = None
    guard_trigger_seconds = None
    with log.open("w") as handle:
        proc = subprocess.Popen(
            time_cmd + command, cwd=cwd, env=env, stdout=handle,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            sample = 0
            while proc.poll() is None:
                group, rustc = group_rss_kib(proc.pid)
                peak_group = max(peak_group, group)
                peak_rustc = max(peak_rustc, rustc)
                elapsed = time.monotonic() - started
                if group >= args.max_rss_gib * 1024**2:
                    reason = "rss_guard"
                elif elapsed >= args.max_seconds:
                    reason = "time_guard"
                elif sample % 5 == 0:
                    disk_free_gib = shutil.disk_usage(cwd).free / 1024**3
                    swap_free = free_swap_mib()
                    memory_free = free_memory_percent()
                    if disk_free_gib < args.min_disk_gib:
                        reason = "disk_guard"
                    elif swap_free is not None and swap_free < args.min_swap_mib:
                        reason = "swap_guard"
                    elif memory_free is not None and memory_free < args.min_memory_percent:
                        reason = "memory_guard"
                if reason:
                    guard_trigger_seconds = elapsed
                    stop_group(proc.pid)
                    break
                sample += 1
                time.sleep(0.2)
        finally:
            if proc.poll() is None:
                stop_group(proc.pid)
            exit_code = proc.wait()
    output = log.read_text(errors="replace")
    pattern = (r"^\s*(\d+)\s+maximum resident set size" if sys.platform == "darwin"
               else r"Maximum resident set size \(kbytes\):\s*(\d+)")
    matches = re.findall(pattern, output, re.M)
    peak_time_bytes = int(matches[-1]) * (1 if sys.platform == "darwin" else 1024) if matches else None
    if sys.platform == "darwin":
        timing = re.search(r"^\s*([0-9.]+)\s+real\b", output, re.M)
        command_seconds = float(timing.group(1)) if timing else None
    else:
        timing = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\):\s*([0-9:.]+)", output)
        parts = timing.group(1).split(":") if timing else []
        command_seconds = sum(float(part) * 60**power for power, part in enumerate(reversed(parts))) if parts else None
    return {
        "seconds": round(time.monotonic() - started, 3),
        "command_seconds": command_seconds,
        "guard_trigger_seconds": round(guard_trigger_seconds, 3) if guard_trigger_seconds is not None else None,
        "exit_code": exit_code,
        "status": reason or ("success" if exit_code == 0 else "failed"),
        "peak_group_rss_bytes_sampled": peak_group * 1024,
        "peak_rustc_rss_bytes_sampled": peak_rustc * 1024,
        "peak_time_rss_bytes": peak_time_bytes,
        "log": str(log),
        "compile_seen": "Compiling bench_i18n " in output,
        "runtime_output": next((line.removeprefix("bench_result ") for line in output.splitlines()
                                if line.startswith("bench_result ")), None),
    }


def write_crate(crate: Path, checkout: Path) -> None:
    (crate / "src").mkdir(parents=True, exist_ok=True)
    (crate / "Cargo.toml").write_text(
        "[package]\nname = \"bench_i18n\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n"
        f"[dependencies]\nrust-i18n = {{ path = {json.dumps(str(checkout))} }}\n\n[workspace]\n"
    )


def set_corpus(crate: Path, corpus: Path, key: str, sample: int) -> None:
    link = crate / "locales"
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.symlink_to(corpus, target_is_directory=True)
    (crate / "src/main.rs").write_text(SOURCE.replace("__KEY__", json.dumps(key)) + f"\n// sample {sample}\n")


def append_result(path: Path, row: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({key: row[key] for key in ("tier", "variant", "sample", "phase", "status", "seconds")}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--toolchain-bin", type=Path)
    parser.add_argument("--tiers", default="small,medium,large,xlarge")
    parser.add_argument("--max-rss-gib", type=float, default=10)
    parser.add_argument("--max-seconds", type=int, default=900)
    parser.add_argument("--min-disk-gib", type=float, default=8)
    parser.add_argument("--min-swap-mib", type=float, default=600)
    parser.add_argument("--min-memory-percent", type=float, default=10)
    parser.add_argument("--runtime-trials", type=int, default=5)
    parser.add_argument("--warm-online", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    output = args.work_dir / "results.jsonl"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    selected = set(args.tiers.split(","))
    env = dict(os.environ, CARGO_BUILD_JOBS="1", CARGO_INCREMENTAL="0")
    if args.toolchain_bin:
        env["PATH"] = str(args.toolchain_bin) + os.pathsep + env.get("PATH", "")
    metadata = {
        "main_sha": git_sha(args.main),
        "candidate_sha": git_sha(args.candidate),
        "rustc": subprocess.check_output(["rustc", "-Vv"], text=True, env=env),
        "cargo": subprocess.check_output(["cargo", "-V"], text=True, env=env).strip(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "tiers": [tier for tier in TIERS if tier[0] in selected],
        "build_env": {key: env[key] for key in ("CARGO_BUILD_JOBS", "CARGO_INCREMENTAL")},
        "guards": {key: getattr(args, key) for key in (
            "max_rss_gib", "max_seconds", "min_disk_gib", "min_swap_mib", "min_memory_percent")},
    }
    (args.work_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    crates = {"main": args.work_dir / "consumer-main", "candidate": args.work_dir / "consumer-candidate"}
    for variant, checkout in (("main", args.main), ("candidate", args.candidate)):
        crate = crates[variant]
        write_crate(crate, checkout)
        warm = args.work_dir / "corpus-warm"
        info = generate(warm, 1, 50)
        set_corpus(crate, warm, info["first_key"], 0)
        lock_command = ["cargo", "generate-lockfile"] + ([] if args.warm_online else ["--offline"])
        subprocess.run(lock_command, cwd=crate, env=env, check=True,
                       stdout=(args.work_dir / f"warm-{variant}-lock.log").open("w"), stderr=subprocess.STDOUT)
        warm_command = ["cargo", "build", "--release", "--locked", "--bin", "bench_i18n"]
        if not args.warm_online:
            warm_command.insert(3, "--offline")
        result = run_guarded(warm_command,
                             crate, env, args.work_dir / f"warm-{variant}.log", args)
        if result["status"] != "success":
            raise SystemExit(f"{variant} warm build failed: {result}")

    for tier, locales, keys, samples in TIERS:
        if tier not in selected:
            continue
        corpus = args.work_dir / f"corpus-{tier}"
        info = generate(corpus, locales, keys)
        info["corpus_sha256"] = sha256_corpus(corpus)
        for sample in range(1, samples + 1):
            order = ("main", "candidate") if sample % 2 else ("candidate", "main")
            if tier == "xlarge":
                order = ("candidate", "main")
            for variant in order:
                crate = crates[variant]
                set_corpus(crate, corpus, info["first_key"], sample)
                result = run_guarded(
                    ["cargo", "build", "--release", "--offline", "--locked", "--bin", "bench_i18n"],
                    crate, env, args.work_dir / f"{tier}-{variant}-build-{sample}.log", args,
                )
                row = dict(info, tier=tier, variant=variant, sample=sample, phase="build", **result)
                if row["status"] == "success" and not row["compile_seen"]:
                    row["status"] = "no_recompile"
                row.pop("runtime_output")
                append_result(output, row)
                if row["status"] != "success":
                    raise SystemExit(
                        f"Stopped after {tier} {variant} build: {row['status']}; see {row['log']}"
                    )
                binary = crate / "target/release/bench_i18n"
                size = binary.stat().st_size
                for trial in range(1, args.runtime_trials + 1):
                    runtime = run_guarded([str(binary)], crate, env,
                                          args.work_dir / f"{tier}-{variant}-runtime-{sample}-{trial}.log", args)
                    value = json.loads(runtime.pop("runtime_output")) if runtime["runtime_output"] else None
                    runtime.pop("compile_seen")
                    append_result(output, dict(info, tier=tier, variant=variant, sample=sample,
                                               trial=trial, phase="runtime", binary_bytes=size,
                                               measurement=value, **runtime))


if __name__ == "__main__":
    main()
