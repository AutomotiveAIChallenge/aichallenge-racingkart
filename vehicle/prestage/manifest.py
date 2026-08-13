#!/usr/bin/env python3
"""Read/write the prestage vault manifest (JSON, stdlib only).

Layout is documented in docs/spec/prestaged-submissions.md.
"""
import argparse
import json
import os
import sys

SCHEMA = 1


def load(path):
    if not os.path.exists(path):
        return {"schema": SCHEMA, "image": {}, "teams": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def find_team(data, team_id):
    for team in data.get("teams", []):
        if team.get("team_id") == team_id:
            return team
    return None


def cmd_init(args):
    data = load(args.manifest)
    data["schema"] = SCHEMA
    data.setdefault("teams", [])
    data["image"] = {"tag": args.image_tag, "id": args.image_id}
    save(args.manifest, data)
    return 0


def cmd_upsert(args):
    data = load(args.manifest)
    entry = {
        "team_id": args.team_id,
        "user_id": args.user_id,
        "submission_id": args.submission_id,
        "submitted_at": args.submitted_at,
        "build_status": args.build_status,
        "install_sha256": args.install_sha256,
        "submission_sha256": args.submission_sha256,
    }
    teams = [t for t in data.get("teams", []) if t.get("team_id") != args.team_id]
    teams.append(entry)
    teams.sort(key=lambda t: t.get("team_id") or "")
    data["teams"] = teams
    save(args.manifest, data)
    return 0


def cmd_get(args):
    team = find_team(load(args.manifest), args.team_id)
    if team is None:
        print(f"team not found: {args.team_id}", file=sys.stderr)
        return 1
    if args.field not in team:
        print(f"field not found: {args.field}", file=sys.stderr)
        return 1
    print(team[args.field] if team[args.field] is not None else "")
    return 0


def cmd_image_id(args):
    print(load(args.manifest).get("image", {}).get("id", ""))
    return 0


def cmd_summary(args):
    for team in load(args.manifest).get("teams", []):
        print(f"{team.get('team_id')}\t{team.get('build_status')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("manifest")
    p.add_argument("--image-id", required=True)
    p.add_argument("--image-tag", default="aichallenge-2025-dev")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("upsert")
    p.add_argument("manifest")
    p.add_argument("--team-id", required=True)
    p.add_argument("--user-id", default="")
    p.add_argument("--submission-id", default="")
    p.add_argument("--submitted-at", type=int, default=0)
    p.add_argument("--build-status", required=True, choices=["ok", "failed"])
    p.add_argument("--install-sha256", default="")
    p.add_argument("--submission-sha256", default="")
    p.set_defaults(func=cmd_upsert)

    p = sub.add_parser("get")
    p.add_argument("manifest")
    p.add_argument("--team-id", required=True)
    p.add_argument("--field", required=True)
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("image-id")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_image_id)

    p = sub.add_parser("summary")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
