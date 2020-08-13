import requests
import csv
from contextlib import closing
import datetime
import json
from mastodon import Mastodon
import argparse
import os
import sys
from collections import namedtuple
from sparklines import sparklines

Source = namedtuple('Source', ['csv_url', 'link_url', 'filter',
                               'values', 'fmt'])
Result = namedtuple('Result', ['date', 'values'])

LAST_UPDATE_FILE = 'last_update.json'
ACCOUNT_FILE = 'account.json'
ETAG_FILE = 'etags.json'
HISTORY_DAYS = 7
SOURCES = {
    'approval': Source(
        'https://projects.fivethirtyeight.com/trump-approval-data/'
        'approval_topline.csv',
        'https://projects.fivethirtyeight.com/trump-approval-ratings/',
        {'subgroup': 'All polls'},
        {'approve': 'approve_estimate', 'disapprove': 'disapprove_estimate'},
        '{:.1f}%',
    ),
    'presmodel': Source(
        'https://projects.fivethirtyeight.com/2020-general-data/'
        'presidential_national_toplines_2020.csv',
        'https://projects.fivethirtyeight.com/2020-election-forecast/',
        {},
        {'Trump': 'ecwin_inc', 'Biden': 'ecwin_chal'},
        '{:.0%}',
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
        headers['If-None-Match'] = etag_data[url]

    res = requests.get(url, headers=headers, stream=True)

    # If the file is unmodified, abort now.
    if res.status_code == 304:
        return None

    # If we have updated content, save the new ETag.
    etag_data[url] = res.headers['ETag']
    with open(os.path.join(basedir, ETAG_FILE), 'w') as f:
        json.dump(etag_data, f)

    return res


def load_model(src, res):
    """Load model results from a Requests response.
    """
    reader = csv.DictReader(res.iter_lines(decode_unicode=True))
    for row in reader:
        if all(row[key] == value for key, value in src.filter.items()):
            yield parse_model_row(src, row)


def parse_model_row(src, row):
    """Take a row dict from the model CSV and produce a Result.
    """
    return Result(
        datetime.datetime.strptime(row['modeldate'], '%m/%d/%Y'),
        {key: float(row[value]) for key, value in src.values.items()},
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


def fmt_change(diff):
    """Format a delta as a string, like +0.1%, -1.2%, or "even" for no
    change.
    """
    if round(abs(diff), 1) < 0.1:
        return 'even'
    else:
        return '{:+.1f}%'.format(diff)


def timespark(values):
    """Draw a one-line Unicode sparkline plot of a sequence of
    reverse-chronological values.
    """
    return sparklines(list(reversed(list(values))))[0]


def get_message(src, basedir):
    """Get the message to be posted, or None if nothing is to be done.
    """
    # Get the latest model data, aborting if unchanged.
    res = etag_get(basedir, src.csv_url)
    if res is None:
        return None
    with closing(res):
        model_data = load_model(src, res)
        latest = next(model_data)

        # Check whether anything has changed.
        fmt_vals = {k: src.fmt.format(latest.values[k])
                    for k in src.values}
        changed = checkpoint(os.path.join(basedir, LAST_UPDATE_FILE), {
            'modeldate': latest.date.timestamp(),
            **fmt_vals
        })
        if not changed:
            return None

        # Get a window of older results.
        history = [latest]
        delta = datetime.timedelta(days=HISTORY_DAYS)
        for oldres in model_data:
            if oldres.date != history[-1].date:
                history.append(oldres)
            if latest.date - oldres.date >= delta:
                break
        prev = history[1]

    # Construct the message.
    msg = 'As of {date}:\n'.format(
        date=latest.date.strftime('%A, %B %-d, %Y'),
    )
    for i, key in enumerate(src.values):
        msg += (
            '{value} {key}\n'
            '{spark} ({chg}{since_date})\n'
        ).format(
            key=key,
            value=fmt_vals[key],
            spark=timespark(h.values[key] for h in history),
            chg=fmt_change(latest.values[key] - prev.values[key]),
            since_date=(
                ' since ' + prev.date.strftime('%-m/%-d')
                if i == 0 else ''
            ),
        )
    msg += src.link_url
    return msg


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
    parser.add_argument('--src', type=str, metavar='NAME', default='approval',
                        help="Data source.")
    args = parser.parse_args()

    # Pick the data source.
    try:
        src = SOURCES[args.src]
    except KeyError:
        print('Data source {} unknown. Must be one of: {}'.format(
            args.src, ', '.join(SOURCES),
        ), file=sys.stderr)
        sys.exit(1)

    # Concoct the message.
    if args.msg:
        msg = args.msg
    else:
        msg = get_message(src, args.dir)
    print(msg or 'No update.')

    # Possibly toot.
    if msg and not args.print:
        toot(args.dir, msg)
        print('Tooted.')


if __name__ == '__main__':
    n4a()
