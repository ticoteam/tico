-- Finance pages read newest first, keyset-paged on (day, id) (backend/finance/store/banking.py feed_page and lines_page).
-- With id in the index a page is a walk down it that stops at the page's end; without it, each page sorted every row of
-- its window first (a payments account's tens of thousands of feed rows, for each page of the Finance tab and Watch).
-- The account and card indexes replace their (…, txn_date) forms, which are their prefixes.
CREATE INDEX IF NOT EXISTS fin_feed_txns_account_day ON fin_feed_txns(money_account_id, txn_date, id);
CREATE INDEX IF NOT EXISTS fin_feed_txns_card_day ON fin_feed_txns(card_id, txn_date, id);
DROP INDEX IF EXISTS fin_feed_txns_account;
DROP INDEX IF EXISTS fin_feed_txns_card;
CREATE INDEX IF NOT EXISTS fin_lines_book_day ON fin_lines(book_id, entry_date, id);
