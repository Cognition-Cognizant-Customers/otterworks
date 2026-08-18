#!/usr/bin/env python3
"""Alarm-gated canary deploys for the portal Lambdas (boto3).

Weighted alias routing on the `live` alias: a new version receives a canary
share of traffic while the per-context error alarm and the gateway 5xx alarm
are watched. If any gate alarm enters ALARM during the bake, the alias is
rolled back to 100% stable automatically — the operator does nothing. If the
gates stay OK for the whole bake, the canary is promoted to 100%.

Terraform ignores alias function_version/routing_config drift (lifecycle
ignore_changes), so canary shifts never fight `terraform apply`.

Usage (good canary → auto-promote):
  python3 canary.py deploy --function ow-tp-portal-demo-feedback \\
      --jar services/portal-serverless/feedback-service/target/feedback-service.jar \\
      --weight 0.1 --bake-seconds 120

Usage (bad canary → auto-rollback; CHAOS_FAULT makes every invocation fail):
  python3 canary.py deploy --function ow-tp-portal-demo-feedback \\
      --jar services/portal-serverless/feedback-service/target/feedback-service.jar \\
      --env CHAOS_FAULT=invoke-error --weight 0.1 --bake-seconds 120

  python3 canary.py status --function ow-tp-portal-demo-feedback
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import boto3

ALIAS = "live"
POLL_SECONDS = 15


def gate_alarm_names(function_name: str) -> list[str]:
    # ow-tp-portal-<ns>-<context> -> per-context errors alarm + gateway 5xx alarm.
    return [f"{function_name}-errors", f"{function_name.rsplit('-', 1)[0]}-api-5xx"]


def alarm_states(cloudwatch, names: list[str]) -> dict[str, str]:
    # DescribeAlarms silently omits unknown alarm names; a missing gate must be
    # a hard failure, never an ungated bake.
    resp = cloudwatch.describe_alarms(AlarmNames=names)
    states = {a["AlarmName"]: a["StateValue"] for a in resp["MetricAlarms"]}
    missing = [n for n in names if n not in states]
    if missing:
        raise SystemExit(f"gate alarms not found (refusing to bake ungated): {missing}")
    return states


def wait_version_active(lam, function_name: str, version: str, timeout: int = 300) -> None:
    # An alias pointed at a Pending version 500s with no metrics; never shift
    # traffic until the published version reports Active (SnapStart ~45-60s).
    deadline = time.time() + timeout
    while time.time() < deadline:
        cfg = lam.get_function_configuration(FunctionName=function_name, Qualifier=version)
        state = cfg.get("State")
        if state == "Active":
            return
        if state == "Failed":
            raise SystemExit(f"version {version} entered Failed state: {cfg.get('StateReason')}")
        print(f"  version {version} is {state}; waiting...")
        time.sleep(10)
    raise SystemExit(f"version {version} not Active after {timeout}s")


def set_alias(lam, function_name: str, stable: str, canary: str | None, weight: float) -> None:
    kwargs = {"FunctionName": function_name, "Name": ALIAS, "FunctionVersion": stable}
    if canary is not None:
        kwargs["RoutingConfig"] = {"AdditionalVersionWeights": {canary: weight}}
    else:
        kwargs["RoutingConfig"] = {"AdditionalVersionWeights": {}}
    lam.update_alias(**kwargs)


def cmd_deploy(args) -> int:
    lam = boto3.client("lambda", region_name=args.region)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)

    alias = lam.get_alias(FunctionName=args.function, Name=ALIAS)
    stable = alias["FunctionVersion"]
    print(f"stable version: {stable}")

    if args.jar:
        with open(args.jar, "rb") as f:
            lam.update_function_code(FunctionName=args.function, ZipFile=f.read(), Publish=False)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=args.function)
    if args.env:
        cfg = lam.get_function_configuration(FunctionName=args.function)
        merged = dict(cfg.get("Environment", {}).get("Variables", {}))
        for pair in args.env:
            key, _, value = pair.partition("=")
            merged[key] = value
        lam.update_function_configuration(FunctionName=args.function,
                                          Environment={"Variables": merged})
        lam.get_waiter("function_updated_v2").wait(FunctionName=args.function)

    canary = lam.publish_version(FunctionName=args.function,
                                 Description=args.description)["Version"]
    print(f"published canary version: {canary}")
    wait_version_active(lam, args.function, canary)

    # Prove the gates exist and are quiet BEFORE any traffic moves.
    gates = gate_alarm_names(args.function)
    pre = alarm_states(cloudwatch, gates)
    already_firing = sorted(n for n, s in pre.items() if s == "ALARM")
    if already_firing:
        raise SystemExit(f"gate alarms already in ALARM, refusing to deploy: {already_firing}")

    set_alias(lam, args.function, stable, canary, args.weight)
    print(f"alias '{ALIAS}': {int((1 - args.weight) * 100)}% v{stable} / "
          f"{int(args.weight * 100)}% v{canary}; gates: {gates}")

    # Any failure during the bake (gate lookup error, throttle, Ctrl-C) must
    # not strand the canary with live traffic: restore stable, then re-raise.
    try:
        deadline = time.time() + args.bake_seconds
        while True:
            states = alarm_states(cloudwatch, gates)
            firing = sorted(n for n, s in states.items() if s == "ALARM")
            remaining = int(deadline - time.time())
            print(f"  gates: {states} ({max(remaining, 0)}s left)")
            if firing:
                set_alias(lam, args.function, stable, None, 0)
                print(f"ROLLED BACK: {firing} in ALARM -> alias '{ALIAS}' restored "
                      f"to 100% v{stable}; canary v{canary} received no further traffic")
                return 2
            if time.time() >= deadline:
                break
            time.sleep(min(POLL_SECONDS, max(1, remaining)))
    except BaseException:
        set_alias(lam, args.function, stable, None, 0)
        print(f"bake aborted -> alias '{ALIAS}' restored to 100% v{stable}")
        raise

    set_alias(lam, args.function, canary, None, 0)
    print(f"PROMOTED: gates stayed OK for {args.bake_seconds}s -> "
          f"alias '{ALIAS}' now 100% v{canary}")
    return 0


def cmd_status(args) -> int:
    lam = boto3.client("lambda", region_name=args.region)
    cloudwatch = boto3.client("cloudwatch", region_name=args.region)
    alias = lam.get_alias(FunctionName=args.function, Name=ALIAS)
    weights = alias.get("RoutingConfig", {}).get("AdditionalVersionWeights", {})
    print(json.dumps({
        "alias": ALIAS,
        "stable_version": alias["FunctionVersion"],
        "additional_version_weights": weights,
        "gate_alarms": alarm_states(cloudwatch, gate_alarm_names(args.function)),
    }, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)

    pd = sub.add_parser("deploy", help="publish a canary, bake it against the "
                                       "alarm gates, then promote or roll back")
    pd.add_argument("--function", required=True, help="full function name "
                    "(e.g. ow-tp-portal-demo-feedback)")
    pd.add_argument("--jar", help="jar to upload as the canary's code "
                    "(omit to canary the current $LATEST code)")
    pd.add_argument("--env", action="append", default=[], metavar="KEY=VALUE",
                    help="environment override applied before publishing "
                         "(e.g. CHAOS_FAULT=invoke-error for the bad-canary beat)")
    pd.add_argument("--weight", type=float, default=0.1,
                    help="canary traffic share, 0-1 (default 0.1)")
    pd.add_argument("--bake-seconds", type=int, default=120,
                    help="bake window; alarm evaluation is 60s/1-period, so "
                         "give the gates at least two periods (default 120)")
    pd.add_argument("--description", default="canary", help="version description")
    pd.add_argument("--region", default="us-east-1")
    pd.set_defaults(fn=cmd_deploy)

    ps = sub.add_parser("status", help="show alias weights and gate alarm states")
    ps.add_argument("--function", required=True)
    ps.add_argument("--region", default="us-east-1")
    ps.set_defaults(fn=cmd_status)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
