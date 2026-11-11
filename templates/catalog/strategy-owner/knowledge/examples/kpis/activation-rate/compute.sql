-- Parameters: :start and :end, the period's first and last day (UTC).
SELECT round(100.0 * sum(CASE WHEN setup_finished_at IS NOT NULL
                              AND julianday(setup_finished_at) - julianday(created_at) <= 7 THEN 1 ELSE 0 END)
             / count(*), 1) AS value,
       count(*) AS accounts
FROM accounts
WHERE is_test = 0 AND date(created_at) BETWEEN :start AND :end;
