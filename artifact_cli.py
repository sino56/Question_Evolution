"""Small command-line bridge for artifact validation and round-input publishing."""

import argparse
import json
import os
import shutil
import sys

from pipeline_runtime import (
    ArtifactPaths,
    iter_json_records,
    publish_records,
    read_manifest,
    sha256_file,
    validate_published_artifact,
)


def validate_command(args: argparse.Namespace) -> int:
    valid, reason = validate_published_artifact(
        args.output,
        stage=args.stage,
        input_path=args.input,
    )
    if not valid:
        print(reason, file=sys.stderr)
        return 1
    return 0


def prepare_round_input_command(args: argparse.Namespace) -> int:
    def records():
        for record in iter_json_records(args.input, stage="prepare_round_input"):
            result = dict(record)
            result["round"] = args.round
            yield result

    publish_records(
        records(),
        args.output,
        stage="prepare_round_input",
        input_path=args.input,
        config={"round": args.round},
        code_paths=[__file__],
        performance_path=args.performance_events,
    )
    return 0


def copy_published_command(args: argparse.Namespace) -> int:
    valid, reason = validate_published_artifact(args.input)
    if not valid:
        print(f"source artifact is invalid: {reason}", file=sys.stderr)
        return 1
    manifest = read_manifest(args.input)
    if not manifest:
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    temporary = args.output + ".tmp"
    shutil.copy2(args.input, temporary)
    os.replace(temporary, args.output)
    manifest = dict(manifest)
    artifact = dict(manifest.get("artifact") or {})
    artifact["path"] = os.path.basename(args.output)
    artifact["bytes"] = os.path.getsize(args.output)
    artifact["sha256"] = sha256_file(args.output)
    manifest["artifact"] = artifact
    copied_sidecars = []
    source_dir = os.path.dirname(os.path.abspath(args.input))
    output_dir = os.path.dirname(os.path.abspath(args.output))
    for sidecar in manifest.get("sidecars") or []:
        entry = dict(sidecar)
        source_path = entry.get("path")
        if not os.path.isabs(source_path):
            source_path = os.path.join(source_dir, source_path)
        target_path = os.path.join(output_dir, os.path.basename(source_path))
        shutil.copy2(source_path, target_path)
        entry["path"] = os.path.basename(target_path)
        entry["bytes"] = os.path.getsize(target_path)
        entry["sha256"] = sha256_file(target_path)
        copied_sidecars.append(entry)
    manifest["sidecars"] = copied_sidecars
    manifest_path = ArtifactPaths(args.output).manifest
    manifest_temporary = manifest_path + ".tmp"
    with open(manifest_temporary, "w", encoding="utf-8") as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    os.replace(manifest_temporary, manifest_path)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and publish pipeline artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--output", required=True)
    validate.add_argument("--stage", required=True)
    validate.add_argument("--input", required=True)
    validate.set_defaults(handler=validate_command)

    prepare = subparsers.add_parser("prepare-round-input")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--round", type=int, required=True)
    prepare.add_argument("--performance-events", default=None)
    prepare.set_defaults(handler=prepare_round_input_command)

    copy_published = subparsers.add_parser("copy-published")
    copy_published.add_argument("--input", required=True)
    copy_published.add_argument("--output", required=True)
    copy_published.set_defaults(handler=copy_published_command)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
