-- fin_vendor_legacy, the vendor names finance phase 1 posted as written (cloud step 66), is retired. Its keys are not
-- carried over: a line posted under such a name no longer counts toward its vendor (finance/store/ap.py vendor_keys).
DROP TABLE IF EXISTS fin_vendor_legacy;
