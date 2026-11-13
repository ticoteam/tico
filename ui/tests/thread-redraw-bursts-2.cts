// Seeds 10-14 of the random bursts in thread-redraw.cts, as their own script so each stays well under the script time limit.
process.env.TICO_REDRAW_SEEDS = '10-14';
require('./thread-redraw.cts');
