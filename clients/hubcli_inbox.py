"""Inbox commands: whoami, alerts, inbound, letters, mail, email, audiences, editions, readers."""

import argparse
import json


def add_inbox(sub):
    sub.add_parser("whoami").set_defaults(fn="whoami")

    alerts = sub.add_parser('alerts', help='your own phone and paid alert preferences').add_subparsers(dest='sub')
    for action in ('show', 'remove', 'confirm', 'verify', 'test', 'set', 'company'):
        item = alerts.add_parser(action)
        item.set_defaults(fn='alerts ' + action)
        if action == 'confirm':
            item.add_argument('code')
        if action == 'verify':
            item.add_argument('phone')
            item.add_argument('--sms-consent', action='store_true', required=True)
            item.add_argument('--call-consent', action='store_true', default=False)
        if action == 'test':
            item.add_argument('--accept-charge', action='store_true', required=True, help='company pays $1 if accepted')
            item.add_argument('--operation-id', required=True, help='stable id for this paid test and retries')
        if action in ('set', 'company'):
            item.add_argument('--monthly-cap', type=int)
        if action == 'set':
            for flag in ('urgent-needs', 'legal-mail', 'bot-stuck', 'urgent-calls', 'emergency-exception'):
                item.add_argument('--' + flag, action=argparse.BooleanOptionalAction, default=None)
            for flag in ('timezone', 'quiet-start', 'quiet-end'):
                item.add_argument('--' + flag)

    inbound = sub.add_parser('inbound', help='shared company email and SMS inboxes').add_subparsers(dest='sub')
    item = inbound.add_parser('inboxes')
    item.set_defaults(fn='inbound inboxes')
    item.add_argument('--after')
    item.add_argument('--limit', type=int)
    item = inbound.add_parser('create-email')
    item.add_argument('name', nargs='?')
    item.set_defaults(fn='inbound create-email')
    for action in ('list', 'search', 'cursor', 'wait'):
        item = inbound.add_parser(action)
        item.set_defaults(fn='inbound ' + action)
        item.add_argument('--inbox-id', dest='inbox_ids', action='append')
        if action == 'search':
            item.add_argument('q')
        if action in ('list', 'search', 'wait'):
            item.add_argument('--kind', choices=('email', 'sms'))
            item.add_argument('--from', dest='sender')
            item.add_argument('--has-code', action='store_true', default=None)
            item.add_argument('--after', type=int, required=action == 'wait')
            item.add_argument('--limit', type=int)
        if action == 'wait':
            item.add_argument('--timeout', type=int)
        if action in ('list', 'search'):
            item.add_argument('--before', type=int)
        if action == 'list':
            item.add_argument('--spam', action='store_true', default=None)
    item = inbound.add_parser('audience')
    item.set_defaults(fn='inbound audience')
    item.add_argument('inbox_id')
    item.add_argument('audience', choices=('company', 'team'))
    item.add_argument('--group')
    for action in ('read', 'code'):
        item = inbound.add_parser(action)
        item.set_defaults(fn='inbound ' + action)
        item.add_argument('message_id')
        if action == 'read':
            item.add_argument('--offset', type=int)
            item.add_argument('--max-chars', type=int)

    letters = sub.add_parser('letter', help='quote and send physical letters through the Gateway').add_subparsers(
        dest='sub'
    )
    item = letters.add_parser('quote', help='quote a PDF or text; never prints until send and approval')
    item.set_defaults(fn='letter quote')
    item.add_argument('--file-id')
    item.add_argument('--text')
    item.add_argument(
        '--recipient', required=True, type=json.loads, help='US address as JSON: name, line1, city, state, postal_code'
    )
    item.add_argument('--mail-class', choices=['first_class', 'certified', 'certified_return_receipt'])
    item.add_argument('--color', action='store_true')
    item.add_argument('--duplex', action='store_true')
    item.add_argument('--task-id')
    item.add_argument('--thread-id')
    item = letters.add_parser('list')
    item.set_defaults(fn='letter list')
    item.add_argument('--before')
    item.add_argument('--limit', type=int)
    for action in ('send', 'show', 'cancel'):
        item = letters.add_parser(action)
        item.set_defaults(fn='letter ' + action)
        item.add_argument('letter_id')
    mail = sub.add_parser('mail', help='physical mail the company received (scanned letters)').add_subparsers(
        dest='sub'
    )
    item = mail.add_parser('list')
    item.set_defaults(fn='mail list')
    item.add_argument('--status', choices=('new', 'filed', 'archived', 'failed'))
    item.add_argument('--kind', choices=('legal', 'government_tax', 'bill', 'check', 'financial', 'junk', 'other'))
    item.add_argument('--after')
    item.add_argument('--limit', type=int)
    item = mail.add_parser('read')
    item.set_defaults(fn='mail read')
    item.add_argument('item_id')

    email = sub.add_parser(
        'email', help='read and organize email, set sending policy, and manage pending sends'
    ).add_subparsers(dest='sub')
    item = email.add_parser('policy')
    item.set_defaults(fn='email policy')
    item.add_argument('--mailbox')
    item.add_argument('--bot')
    item.add_argument('--settings', type=json.loads, help='JSON policy fields; omit to read')
    item = email.add_parser('sends')
    item.set_defaults(fn='email sends')
    item.add_argument('--mailbox', required=True)
    item.add_argument('--pending', action='store_true')
    item.add_argument('--cursor')
    item.add_argument('--limit', type=int)
    item = email.add_parser('cancel')
    item.set_defaults(fn='email cancel')
    item.add_argument('id')
    for action in ('mailboxes', 'inbox', 'search', 'thread', 'message'):
        item = email.add_parser(action)
        item.set_defaults(fn='email ' + action)
        item.add_argument('--json', action='store_true', help='print the API response as JSON')
        if action != 'mailboxes':
            item.add_argument('mailbox', metavar='address')
        if action in ('inbox', 'search'):
            item.add_argument('--limit', type=int, default=50)
            item.add_argument('--cursor', help='opaque next_cursor from the previous page')
            if action == 'inbox':
                item.add_argument('--q', default='', help='Gmail search syntax within the inbox')
            else:
                item.add_argument('q', help='Gmail search syntax')
        if action in ('thread', 'message'):
            item.add_argument('id', metavar=action + '-id')
    for action in ('archive', 'unarchive', 'mark-read', 'mark-unread', 'label', 'unlabel'):
        item = email.add_parser(action)
        item.set_defaults(fn='email ' + action)
        item.add_argument('mailbox', metavar='address')
        if action in ('label', 'unlabel'):
            item.add_argument('label')
        item.add_argument('ids', nargs='*', metavar='message-id')
        item.add_argument('--query', help='Gmail query instead of explicit message ids')
        item.add_argument('--limit', type=int, default=100, help='query cap, 1 to 500 (default 100)')
        item.add_argument('--json', action='store_true', help='print the API response as JSON')

    # The Company page, read-only (docs/company-page.md): owners and admins; nothing here publishes or changes a reader.
    sub.add_parser('company-page', help='the Company page: transparency, address, last update').set_defaults(
        fn='company-page'
    )
    audiences = sub.add_parser('audiences', help='the Company page\'s audiences').add_subparsers(dest='sub')
    item = audiences.add_parser('list')
    item.set_defaults(fn='audiences list')
    item.add_argument('--limit', type=int)
    item.add_argument('--offset', type=int)
    editions = sub.add_parser('editions', help='the Company page\'s editions: drafts and published').add_subparsers(
        dest='sub'
    )
    item = editions.add_parser('list')
    item.set_defaults(fn='editions list')
    item.add_argument('--audience')
    item.add_argument('--status')
    item.add_argument('--before')
    item.add_argument('--limit', type=int)
    item = editions.add_parser('show')
    item.set_defaults(fn='editions show')
    item.add_argument('id')
    readers = sub.add_parser('readers', help='the Company page\'s readers (owners and admins)').add_subparsers(
        dest='sub'
    )
    item = readers.add_parser('list')
    item.set_defaults(fn='readers list')
    item.add_argument('--limit', type=int)
    item.add_argument('--cursor', type=int)
