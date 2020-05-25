import requests
import csv
from contextlib import closing
from datetime import datetime
import json
from mastodon import Mastodon
import argparse
import os
from collections import namedtuple

CSV_URL = 'https://projects.fivethirtyeight.com/trump-approval-data/' \
    'approval_topline.csv'
LINK_URL = 'https://projects.fivethirtyeight.com/trump-approval-ratings/'
SUBGROUP = 'All polls'
LAST_UPDATE_FILE = 'last_update.json'
ACCOUNT_FILE = 'account.json'

Result = namedtuple('Result', ['date', 'approve', 'disapprove'])


def get_model():
    """Load model results from the 538 server.
    """
    with closing(requests.get(CSV_URL, stream=True)) as req:
        reader = csv.DictReader(req.iter_lines(decode_unicode=True))
        for row in reader:
            if row['subgroup'] == SUBGROUP:
                yield parse_model_row(row)


def parse_model_row(row):
    """Take a row dict from the model CSV and produce a Result.
    """
    return Result(
        datetime.strptime(row['modeldate'], '%m/%d/%Y'),
        float(row['approve_estimate']),
        float(row['disapprove_estimate']),
    )


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

    with open(filename, 'w') as f:
        json.dump(data, f)

    return changed


def get_message(basedir):
    """Get the message to be posted, or None if nothing is to be done.
    """
    # Get the latest model data.
    model_data = get_model()
    latest = next(model_data)

    # Check whether anything has changed.
    changed = checkpoint(os.path.join(basedir, LAST_UPDATE_FILE), {
        'modeldate': latest.date.timestamp(),
        'approve': '{:.1f}'.format(latest.approve),
        'disapprove': '{:.1f}'.format(latest.disapprove),
    })
    if not changed:
        return None

    # Construct the message.
    return 'As of {}:\n{:.1f}% approve\n{:.1f}% disapprove\n{}'.format(
        latest.date.strftime('%A, %B %-d, %Y'),
        latest.approve,
        latest.disapprove,
        LINK_URL,
    )


def toot(basedir, message):
    with open(os.path.join(basedir, ACCOUNT_FILE)) as f:
        account_data = json.load(f)

    mast = Mastodon(
        api_base_url=account_data['url'],
        access_token=account_data['token'],
    )
    mast.toot(message)


def n4a():
    parser = argparse.ArgumentParser()
    parser.add_argument('--print', action='store_true', default=False,
                        help="Just print the update (don't toot).")
    parser.add_argument('--msg', type=str, metavar='TXT',
                        help="Post this message instead of real data.")
    parser.add_argument('--dir', type=str, metavar='PATH', default='.',
                        help="Base directory for data files (default: cwd).")
    args = parser.parse_args()

    if args.msg:
        msg = args.msg
    else:
        msg = get_message(args.dir)
    print(msg or 'No update.')

    if msg and not args.print:
        toot(args.dir, msg)
        print('Tooted.')


if __name__ == '__main__':
    n4a()
