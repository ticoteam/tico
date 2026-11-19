-- The Get started panel is gone (ui/app/growth-start.ts is the Growth Manager row now), and with it the one-time
-- "you're set up" note: nothing reads or writes when it was shown. Running this again changes nothing.
DROP TABLE IF EXISTS growth_setup_shown;
