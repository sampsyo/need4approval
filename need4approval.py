"""A Mastodon bot for posting polling averages."""

import argparse
import csv
import datetime
import itertools
import json
import os
import sys
from collections import namedtuple
from contextlib import closing

import requests
from mastodon import Mastodon
from sparklines import sparklines

__version__ = "0.2.0"

Source = namedtuple(
    "Source",
    [
        "csv_url",
        "link_url",
        "values",
        "fmt",
        "diff_fmt",
        "digits",
        "one_side",
    ],
)
Result = namedtuple("Result", ["date", "values"])

LAST_UPDATE_FILE = "last_update.json"
ACCOUNT_FILE = "account.json"
ETAG_FILE = "etags.json"
HISTORY_DAYS = 14
SOURCES = {
    "approval": Source(
        "https://www.nytimes.com/newsgraphics/polls/approval/president-averages.csv",
        "https://www.nytimes.com/interactive/polls/"
        "donald-trump-approval-rating-polls.html",
        {"approve": "Approve", "disapprove": "Disapprove"},
        "{:.1f}%",
        "{:+.1f}%",
        1,
        False,
    ),
}


def etag_get(basedir, url):
    """Send a (streaming) GET request for a URL while caching the ETag
    for the response. Return the response, unless the content is
    unmodified since the last request, in which case return None.
    """
    # Load stored ETags.
    try:
        with open(os.path.join(basedir, ETAG_FILE)) as f:
            etag_data = json.load(f)
    except (IOError, json.JSONDecodeError):
        etag_data = {}

    headers = {}
    if url in etag_data:
        headers["If-None-Match"] = etag_data[url]

    res = requests.get(url, headers=headers, stream=True)

    # If the file is unmodified, abort now.
    if res.status_code == 304:
        return None

    # If we have updated content, save the new ETag.
    etag_data[url] = res.headers["ETag"]
    with open(os.path.join(basedir, ETAG_FILE), "w") as f:
        json.dump(etag_data, f)

    return res


def load_model(src, res):
    """Load model results from a Requests response."""
    reader = csv.DictReader(res.iter_lines(decode_unicode=True))
    for rows in itertools.batched(reader, len(src.values), strict=True):
        yield parse_model_rows(src, rows)


def parse_model_rows(src, rows):
    """Take a row dict from the model CSV and produce a Result."""
    assert len(rows) == len(src.values)
    assert len(set(row["date"] for row in rows)) == 1

    date = datetime.datetime.strptime(rows[0]["date"], "%Y-%m-%d")
    answer_to_key = {v: k for k, v in src.values.items()}
    values = {answer_to_key[row["answer"]]: float(row["pct"]) for row in rows}

    return Result(date, values)


def checkpoint(filename, data):
    """Check whether the dict `data` differs from the last time this
    function was called, saving the result as JSON in `filename`.
    """
    try:
        with open(filename) as f:
            old_data = json.load(f)
    except (IOError, json.JSONDecodeError):
        changed = True
    else:
        changed = not all(data[k] == old_data[k] for k in data)

    with open(filename, "w") as f:
        json.dump(data, f)

    return changed


def fmt_change(diff, src):
    """Format a delta as a string, like +0.1%, -1.2%, or "even" for no
    change.
    """
    if round(abs(diff), src.digits) < 10 ** (-src.digits):
        return "even"
    else:
        return src.diff_fmt.format(diff)


def timespark(values):
    """Draw a one-line Unicode sparkline plot of a sequence of
    reverse-chronological values.
    """
    return sparklines(list(reversed(list(values))))[0]


def get_message(src, basedir, skip_days=0):
    """Get the message to be posted, or None if nothing is to be done."""
    # Get the latest model data, aborting if unchanged.
    res = etag_get(basedir, src.csv_url)
    if res is None:
        return None
    with closing(res):
        model_data = list(load_model(src, res))
        model_data.reverse()
        model_data = model_data[skip_days:]
        latest = model_data[0]

        # Check whether anything has changed.
        fmt_vals = {k: src.fmt.format(latest.values[k]) for k in src.values}
        changed = checkpoint(
            os.path.join(basedir, LAST_UPDATE_FILE),
            {"modeldate": latest.date.timestamp(), **fmt_vals},
        )
        if not changed:
            return None

        # Get a window of older results.
        history = [latest]
        delta = datetime.timedelta(days=HISTORY_DAYS)
        for oldres in model_data[1:]:
            if oldres.date != history[-1].date:
                history.append(oldres)
            if latest.date - oldres.date >= delta:
                break

        # `prev` is the date we'll compare against to show a recent trend. This
        # selects yesterday's numbers.
        prev = history[1]

    # In one_side mode, pick only the maximum statistic to show.
    if src.one_side:
        value_keys = [max(src.values, key=lambda k: latest.values[k])]
    else:
        value_keys = src.values

    # Construct the message.
    msg = "As of {date}:\n".format(
        date=latest.date.strftime("%A, %B %-d, %Y"),
    )
    for i, key in enumerate(value_keys):
        msg += ("{value} {key}\n{spark} ({chg}{since_date})\n").format(
            key=key,
            value=fmt_vals[key],
            spark=timespark(h.values[key] for h in history),
            chg=fmt_change(latest.values[key] - prev.values[key], src),
            since_date=(" since " + prev.date.strftime("%-m/%-d") if i == 0 else ""),
        )
    msg += src.link_url
    return msg


def toot(basedir, message):
    with open(os.path.join(basedir, ACCOUNT_FILE)) as f:
        account_data = json.load(f)

    mast = Mastodon(
        api_base_url=account_data["url"],
        access_token=account_data["token"],
    )
    mast.toot(message)


def n4a():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--print",
        action="store_true",
        default=False,
        help="Just print the update (don't toot).",
    )
    parser.add_argument(
        "--msg",
        type=str,
        metavar="TXT",
        help="Post this message instead of real data.",
    )
    parser.add_argument(
        "--dir",
        type=str,
        metavar="PATH",
        default=".",
        help="Base directory for data files (default: cwd).",
    )
    parser.add_argument(
        "--src",
        type=str,
        metavar="NAME",
        default="approval",
        help="Data source.",
    )
    parser.add_argument(
        "--skip",
        type=int,
        metavar="DAYS",
        default=0,
        help="Use data from some days ago.",
    )
    args = parser.parse_args()

    # Pick the data source.
    try:
        src = SOURCES[args.src]
    except KeyError:
        print(
            "Data source {} unknown. Must be one of: {}".format(
                args.src,
                ", ".join(SOURCES),
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    # Concoct the message.
    if args.msg:
        msg = args.msg
    else:
        msg = get_message(src, args.dir, skip_days=args.skip)
    print(msg or "No update.")

    # Possibly toot.
    if msg and not args.print:
        toot(args.dir, msg)
        print("Tooted.")


if __name__ == "__main__":
    n4a()
