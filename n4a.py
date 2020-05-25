import requests
import csv
from contextlib import closing
from datetime import datetime

CSV_URL = 'https://projects.fivethirtyeight.com/trump-approval-data/' \
    'approval_topline.csv'
LINK_URL = 'https://projects.fivethirtyeight.com/trump-approval-ratings/'
SUBGROUP = 'All polls'


def get_model():
    with closing(requests.get(CSV_URL, stream=True)) as req:
        reader = csv.DictReader(req.iter_lines(decode_unicode=True))
        for row in reader:
            if row['subgroup'] == SUBGROUP:
                yield row


def n4a():
    latest = next(get_model())
    modeldate = datetime.strptime(latest['modeldate'], '%m/%d/%Y')
    approve = float(latest['approve_estimate'])
    disapprove = float(latest['disapprove_estimate'])

    msg = 'As of {}:\n{:.1f}% approve\n{:.1f}% disapprove\n{}'.format(
        modeldate.strftime('%A, %B %-d, %Y'),
        approve,
        disapprove,
        LINK_URL,
    )
    print(msg)


if __name__ == '__main__':
    n4a()
