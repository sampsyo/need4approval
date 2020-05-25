import requests
import csv
from contextlib import closing
from datetime import datetime
import json
from mastodon import Mastodon
import argparse

CSV_URL = 'https://projects.fivethirtyeight.com/trump-approval-data/' \
    'approval_topline.csv'
LINK_URL = 'https://projects.fivethirtyeight.com/trump-approval-ratings/'
SUBGROUP = 'All polls'
LAST_UPDATE_FILE = 'last_update.json'
ACCOUNT_FILE = 'account.json'


def get_model():
    with closing(requests.get(CSV_URL, stream=True)) as req:
        reader = csv.DictReader(req.iter_lines(decode_unicode=True))
        for row in reader:
            if row['subgroup'] == SUBGROUP:
                yield row


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


def get_message():
    """Get the message to be posted, or None if nothing is to be done.
    """
    # Get the latest model data.
    latest = next(get_model())
    modeldate = datetime.strptime(latest['modeldate'], '%m/%d/%Y')
    approve = float(latest['approve_estimate'])
    disapprove = float(latest['disapprove_estimate'])

    # Check whether anything has changed.
    changed = checkpoint(LAST_UPDATE_FILE, {
        'modeldate': modeldate.timestamp(),
        'approve': '{:.1f}'.format(approve),
        'disapprove': '{:.1f}'.format(disapprove),
    })
    if not changed:
        return None

    # Construct the message.
    return 'As of {}:\n{:.1f}% approve\n{:.1f}% disapprove\n{}'.format(
        modeldate.strftime('%A, %B %-d, %Y'),
        approve,
        disapprove,
        LINK_URL,
    )


def toot(message):
    with open(ACCOUNT_FILE) as f:
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
    args = parser.parse_args()

    if args.msg:
        msg = args.msg
    else:
        msg = get_message()
    print(msg or 'No update.')

    if msg and not args.print:
        toot(msg)
        print('Tooted.')


if __name__ == '__main__':
    n4a()
