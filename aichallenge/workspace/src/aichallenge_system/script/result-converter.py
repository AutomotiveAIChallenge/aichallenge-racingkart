import argparse
import json


def create_laps(data):
    return data["laps"]


def create_min_time(data):
    if len(data["laps"]) == 0:
        return None
    return min(data["laps"])


def create_total_lap_time(data):
    if len(data["laps"]) == 0:
        return None
    return sum(data["laps"])


def create_num_laps(data):
    return len(data["laps"])


parser = argparse.ArgumentParser()
parser.add_argument("--input", default="result-details.json")
parser.add_argument("--output", default="result-summary.json")

args = parser.parse_args()

with open(args.input) as fp:
    details = json.load(fp)

summary = {
    "laps": create_laps(details),
    "min_time": create_min_time(details),
    "total_lap_time": create_total_lap_time(details),
    "num_laps": create_num_laps(details),
}

with open(args.output, "w") as fp:
    json.dump(summary, fp, indent=4)
    fp.write("\n")
